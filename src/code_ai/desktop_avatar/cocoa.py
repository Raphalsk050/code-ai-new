"""The desktop avatar's window on macOS, what desktop-avatar/ is elsewhere.

    python -m code_ai.desktop_avatar.cocoa <page.html> [<data dir>]

The same contract as the Rust host: a borderless, transparent window over the
main screen's work area, above other windows and on every Space; clicks go
through it except on the face; each stdin line goes to the page's
``desk.receive``, each ``out:`` the page posts comes back on stdout, and the
end of stdin closes it. The data dir is the Rust host's; WebKit here keeps its
own under ~/Library.

PyObjC is imported here and nowhere else. The Mac build carries it; there is
no Mac build to compile a Rust host in, so this one is Python.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

# How often the pointer is read: for the eyes, and for where a click lands.
POLL_S = 1 / 60

# window.ipc as wry gives it, so the page is the same on every platform.
IPC_SHIM = (
    "window.ipc={postMessage:function(m)"
    "{window.webkit.messageHandlers.ipc.postMessage(String(m))}};"
)


class Face:
    """Where the face is, in CSS px inside the window, and whether it is held."""

    def __init__(self) -> None:
        self.x = self.y = self.w = self.h = 0.0
        self.held = False

    def update(self, spec: str) -> None:
        try:
            x, y, w, h, held = (float(part) for part in spec.split(","))
        except ValueError:
            return
        self.x, self.y, self.w, self.h, self.held = x, y, w, h, held != 0

    def covers(self, x: float, y: float) -> bool:
        return self.held or (self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h)


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        usage = "usage: python -m code_ai.desktop_avatar.cocoa <page.html> [<data dir>]"
        print(usage, file=sys.stderr)
        return 2
    html = Path(argv[0]).read_text(encoding="utf-8")

    import AppKit
    import Foundation
    import objc
    import WebKit
    from PyObjCTools import AppHelper

    face = Face()
    state = {"ready": False, "through": True, "last": (None, None)}
    waiting: list[str] = []

    class AvatarWindow(AppKit.NSWindow):
        # Borderless windows refuse to be key by default, and the page could not take a key.
        def canBecomeKeyWindow(self) -> bool:
            return True

    class AvatarView(WebKit.WKWebView):
        # The first press on the face picks it up, rather than only bringing the window forward.
        def acceptsFirstMouse_(self, event: object) -> bool:
            return True

    class Posts(Foundation.NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
        def userContentController_didReceiveScriptMessage_(  # noqa: N802 - the selector
            self, controller: object, message: object
        ) -> None:
            body = str(message.body())
            if body.startswith("out:"):
                sys.stdout.write(body[4:] + "\n")
                sys.stdout.flush()
            elif body.startswith("face:"):
                face.update(body[5:])
            elif body == "ready":
                state["ready"] = True
                # Where this window is, in physical pixels from the main screen's
                # top left: the panel says where its logo is on the screen in those.
                scale = window.backingScaleFactor()
                at = window.frame()
                main = AppKit.NSScreen.screens()[0].frame()
                top = main.size.height - (at.origin.y + at.size.height)
                frame = f"{at.origin.x * scale:.0f},{top * scale:.0f},{scale}"
                script = f"window.desk&&desk.frame({frame})"
                view.evaluateJavaScript_completionHandler_(script, None)
                for line in waiting:
                    deliver(line)
                waiting.clear()

    class Ticker(Foundation.NSObject):
        def tick_(self, timer: object) -> None:
            at = AppKit.NSEvent.mouseLocation()
            frame = window.frame()
            # Screen points from the bottom left, to CSS px from the window's top left.
            x = at.x - frame.origin.x
            y = frame.size.height - (at.y - frame.origin.y)
            if face.covers(x, y) == state["through"]:
                state["through"] = not state["through"]
                window.setIgnoresMouseEvents_(state["through"])
            if state["through"] and (x, y) != state["last"]:
                state["last"] = (x, y)
                script = f"window.desk&&desk.pointer({x:.1f},{y:.1f})"
                view.evaluateJavaScript_completionHandler_(script, None)

    def deliver(line: str) -> None:
        view.evaluateJavaScript_completionHandler_(f"window.desk&&desk.receive({line})", None)

    def heard(line: str) -> None:
        if state["ready"]:
            deliver(line)
        else:
            waiting.append(line)

    app = AppKit.NSApplication.sharedApplication()
    # No Dock icon and no menu bar of its own: it is part of whatever started it.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    frame = AppKit.NSScreen.mainScreen().visibleFrame()
    window = AvatarWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False
    )
    window.setOpaque_(False)
    window.setBackgroundColor_(AppKit.NSColor.clearColor())
    window.setHasShadow_(False)
    window.setLevel_(AppKit.NSFloatingWindowLevel)
    window.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorStationary
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        | AppKit.NSWindowCollectionBehaviorIgnoresCycle
    )
    window.setIgnoresMouseEvents_(True)

    config = WebKit.WKWebViewConfiguration.alloc().init()
    posts = Posts.alloc().init()
    config.userContentController().addScriptMessageHandler_name_(posts, "ipc")
    config.userContentController().addUserScript_(
        WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            IPC_SHIM, WebKit.WKUserScriptInjectionTimeAtDocumentStart, True
        )
    )
    view = AvatarView.alloc().initWithFrame_configuration_(
        Foundation.NSMakeRect(0, 0, frame.size.width, frame.size.height), config
    )
    view.setValue_forKey_(False, "drawsBackground")
    window.setContentView_(view)
    view.loadHTMLString_baseURL_(html, None)
    window.orderFrontRegardless()

    ticker = Ticker.alloc().init()
    Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        POLL_S, ticker, "tick:", None, True
    )

    def read() -> None:
        for line in sys.stdin:
            if line.strip():
                AppHelper.callAfter(heard, line.strip())
        AppHelper.callAfter(app.terminate_, None)

    threading.Thread(target=read, daemon=True).start()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
