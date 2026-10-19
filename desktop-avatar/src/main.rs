//! The Code-AI avatar loose on the desktop, for Windows and Linux; macOS has
//! its own in src/code_ai/desktop_avatar/cocoa.py.
//!
//!     code-ai-avatar <page.html> [<data dir>]
//!
//! One transparent, borderless, always-on-top window over the work area of
//! every screen, showing the page it is given. Clicks go through it everywhere
//! but on the face. Each stdin line is a JSON object for the page's `desk.receive`; each
//! `out:<json>` the page posts comes back as a stdout line. The end of stdin
//! closes it, so it never outlives what started it. The web view keeps its
//! profile in the data dir: beside the executable, where it would go by
//! default, may be a one-file build's temporary folder or not writable at all.

#![cfg_attr(windows, windows_subsystem = "windows")]

use std::error::Error;
use std::io::{BufRead, Write};
use std::time::{Duration, Instant};

use tao::{
    dpi::{PhysicalPosition, PhysicalSize},
    event::{Event, StartCause, WindowEvent},
    event_loop::{ControlFlow, EventLoop, EventLoopBuilder},
    window::{Window, WindowBuilder},
};
use wry::{WebContext, WebView, WebViewBuilder};

/// How often the pointer is read: for the eyes, and for where a click lands.
const POLL: Duration = Duration::from_millis(16);

#[derive(Clone, Copy, Default)]
struct Face {
    /// In CSS px inside the window.
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    /// Carried: it takes the pointer wherever the pointer goes.
    held: bool,
}

impl Face {
    fn covers(&self, x: f64, y: f64) -> bool {
        self.held || (x >= self.x && x <= self.x + self.w && y >= self.y && y <= self.y + self.h)
    }
}

enum Ask {
    Face(Face),
    /// The page can hear the backend now.
    Ready,
    /// A line from the backend, for the page.
    Line(String),
    Quit,
}

fn main() {
    let mut args = std::env::args().skip(1);
    let Some(page) = args.next() else {
        eprintln!("usage: code-ai-avatar <page.html> [<data dir>]");
        std::process::exit(2);
    };
    let data = args.next().map(std::path::PathBuf::from);
    let html = match std::fs::read_to_string(&page) {
        Ok(html) => html,
        Err(err) => {
            eprintln!("{page}: {err}");
            std::process::exit(2);
        }
    };
    #[cfg(target_os = "linux")]
    {
        // Wayland lets no window keep itself on top, put itself somewhere or see
        // the pointer outside it. XWayland does all three, so it wins over a
        // GDK_BACKEND=wayland handed down from the editor.
        if std::env::var_os("DISPLAY").is_some() {
            std::env::set_var("GDK_BACKEND", "x11");
        }
        // Handed DMA-BUFs, GTK 3 paints the window through GL, and on some
        // drivers what earlier frames left there stays: the avatar leaves a
        // copy of itself wherever it passes. Shared memory is painted with
        // cairo over a cleared window.
        if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
            std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
    }
    if let Err(err) = run(html, data) {
        eprintln!("{err}");
        std::process::exit(1);
    }
}

fn run(html: String, data: Option<std::path::PathBuf>) -> Result<(), Box<dyn Error>> {
    let event_loop = EventLoopBuilder::<Ask>::with_user_event().build();
    // After the loop: it is what makes the process DPI-aware, and starts GTK.
    let screens = work_areas(&event_loop);
    let all = span(&screens);
    let size = PhysicalSize::new(all.w as u32, all.h as u32);
    let builder = WindowBuilder::new()
        .with_title("Code-AI")
        .with_decorations(false)
        .with_transparent(true)
        .with_always_on_top(true)
        .with_focused(false)
        .with_resizable(false)
        .with_position(PhysicalPosition::new(all.x, all.y))
        .with_inner_size(size);
    #[cfg(windows)]
    let builder = {
        use tao::platform::windows::WindowBuilderExtWindows;
        builder.with_skip_taskbar(true).with_undecorated_shadow(false)
    };
    #[cfg(target_os = "linux")]
    let builder = {
        use tao::platform::unix::WindowBuilderExtUnix;
        // Shown once it is out of the window manager's hands, below.
        builder.with_skip_taskbar(true).with_visible(false)
    };
    let window = builder.build(&event_loop)?;
    #[cfg(target_os = "linux")]
    {
        // Unmanaged, as a menu is: no window manager raises another window
        // over it, keeps it to one screen or moves it.
        use gtk::prelude::WidgetExt;
        use tao::platform::unix::WindowExtUnix;
        let gtk = window.gtk_window();
        gtk.realize();
        if let Some(gdk) = gtk.window() {
            gdk.set_override_redirect(true);
        }
    }
    // After the loop too: on Linux it is a WebKitGTK object, and GTK must be up.
    let mut context = WebContext::new(data);
    let webview = webview(&window, html, &event_loop, &mut context)?;
    #[cfg(target_os = "linux")]
    window.set_visible(true);

    let lines = event_loop.create_proxy();
    std::thread::spawn(move || {
        for line in std::io::stdin().lock().lines() {
            let Ok(line) = line else { break };
            if !line.trim().is_empty() && lines.send_event(Ask::Line(line)).is_err() {
                return;
            }
        }
        let _ = lines.send_event(Ask::Quit);
    });

    let _ = window.set_ignore_cursor_events(true);
    let mut face = Face::default();
    let mut through = true;
    let mut ready = false;
    let mut waiting: Vec<String> = Vec::new();
    let mut last = (f64::NAN, f64::NAN);
    event_loop.run(move |event, _, flow| {
        // Held for as long as the web view lives.
        let _ = &context;
        *flow = ControlFlow::WaitUntil(Instant::now() + POLL);
        match event {
            Event::UserEvent(Ask::Face(at)) => face = at,
            Event::UserEvent(Ask::Ready) => {
                ready = true;
                frame(&window, &webview, &screens);
                for line in waiting.drain(..) {
                    deliver(&webview, &line);
                }
            }
            // A window over screens of different densities is given the size
            // one of them would want: it keeps its own, and the page its scale.
            Event::WindowEvent { event: WindowEvent::ScaleFactorChanged { new_inner_size, .. }, .. } => {
                *new_inner_size = size;
                if ready {
                    frame(&window, &webview, &screens);
                }
            }
            Event::WindowEvent { event: WindowEvent::Moved(_), .. } if ready => frame(&window, &webview, &screens),
            Event::UserEvent(Ask::Line(line)) if ready => deliver(&webview, &line),
            Event::UserEvent(Ask::Line(line)) => waiting.push(line),
            Event::UserEvent(Ask::Quit)
            | Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => *flow = ControlFlow::Exit,
            Event::NewEvents(StartCause::ResumeTimeReached { .. }) => {
                let Ok(at) = window.cursor_position() else { return };
                let origin = window.inner_position().unwrap_or_default();
                let scale = window.scale_factor();
                let x = (at.x - origin.x as f64) / scale;
                let y = (at.y - origin.y as f64) / scale;
                if face.covers(x, y) == through {
                    through = !through;
                    let _ = window.set_ignore_cursor_events(through);
                }
                // Off the face the page hears nothing of the pointer: it is told, for the eyes.
                if through && (x, y) != last {
                    last = (x, y);
                    let _ = webview.evaluate_script(&format!("window.desk&&desk.pointer({x:.1},{y:.1})"));
                }
            }
            _ => {}
        }
    });
}

fn webview(
    window: &Window,
    html: String,
    event_loop: &EventLoop<Ask>,
    context: &mut WebContext,
) -> wry::Result<WebView> {
    let proxy = event_loop.create_proxy();
    let builder = WebViewBuilder::new_with_web_context(context)
        .with_transparent(true)
        .with_html(html)
        .with_ipc_handler(move |request| {
            let body = request.body();
            if let Some(json) = body.strip_prefix("out:") {
                let mut out = std::io::stdout().lock();
                let _ = writeln!(out, "{json}");
                let _ = out.flush();
            } else if let Some(at) = body.strip_prefix("face:") {
                let v: Vec<f64> = at.split(',').filter_map(|part| part.parse().ok()).collect();
                if let [x, y, w, h, held] = v[..] {
                    let _ = proxy.send_event(Ask::Face(Face { x, y, w, h, held: held != 0.0 }));
                }
            } else if body == "ready" {
                let _ = proxy.send_event(Ask::Ready);
            }
        });
    #[cfg(target_os = "linux")]
    {
        use tao::platform::unix::WindowExtUnix;
        use wry::WebViewBuilderExtUnix;
        builder.build_gtk(window.default_vbox().expect("tao's default vbox"))
    }
    #[cfg(not(target_os = "linux"))]
    builder.build(window)
}

fn deliver(webview: &WebView, line: &str) {
    let _ = webview.evaluate_script(&format!("window.desk&&desk.receive({line})"));
}

/// Tells the page where this window is and where the screens are, in physical
/// pixels: the panel says where its logo is in those, and the page keeps the
/// avatar on a screen rather than in the gaps between them.
fn frame(window: &Window, webview: &WebView, screens: &[Area]) {
    let at = window.inner_position().unwrap_or_default();
    let scale = window.scale_factor();
    let list: Vec<String> = screens.iter().map(|a| format!("[{},{},{},{}]", a.x, a.y, a.w, a.h)).collect();
    let _ = webview.evaluate_script(&format!(
        "window.desk&&desk.frame({},{},{scale},[{}])",
        at.x,
        at.y,
        list.join(",")
    ));
}

/// A screen's work area, in physical pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Area {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
}

/// The smallest area holding them all.
fn span(areas: &[Area]) -> Area {
    let left = areas.iter().map(|a| a.x).min().unwrap_or(0);
    let top = areas.iter().map(|a| a.y).min().unwrap_or(0);
    let right = areas.iter().map(|a| a.x + a.w).max().unwrap_or(1280);
    let bottom = areas.iter().map(|a| a.y + a.h).max().unwrap_or(720);
    Area { x: left, y: top, w: right - left, h: bottom - top }
}

/// The main one first: an avatar with no place of its own starts there.
fn main_first(mut found: Vec<(bool, Area)>, otherwise: impl FnOnce() -> Vec<Area>) -> Vec<Area> {
    found.sort_by_key(|(main, _)| !main);
    let areas: Vec<Area> = found.into_iter().map(|(_, area)| area).collect();
    if areas.is_empty() {
        otherwise()
    } else {
        areas
    }
}

/// Every screen less its taskbar.
#[cfg(windows)]
fn work_areas<T>(event_loop: &EventLoop<T>) -> Vec<Area> {
    use tao::platform::windows::MonitorHandleExtWindows;
    use windows_sys::Win32::{
        Graphics::Gdi::{GetMonitorInfoW, MONITORINFO},
        UI::WindowsAndMessaging::MONITORINFOF_PRIMARY,
    };
    let found = event_loop
        .available_monitors()
        .filter_map(|monitor| {
            let mut info: MONITORINFO = unsafe { std::mem::zeroed() };
            info.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
            if unsafe { GetMonitorInfoW(monitor.hmonitor() as _, &mut info) } == 0 {
                return None;
            }
            let r = info.rcWork;
            let area = Area { x: r.left, y: r.top, w: r.right - r.left, h: r.bottom - r.top };
            Some((info.dwFlags & MONITORINFOF_PRIMARY != 0, area))
        })
        .collect();
    main_first(found, || whole_screens(event_loop))
}

/// Every screen less its panels.
#[cfg(target_os = "linux")]
fn work_areas<T>(event_loop: &EventLoop<T>) -> Vec<Area> {
    use gtk::prelude::MonitorExt;
    let Some(display) = gtk::gdk::Display::default() else {
        return whole_screens(event_loop);
    };
    let main = display.primary_monitor().or_else(|| display.monitor(0));
    let found = (0..display.n_monitors())
        .filter_map(|i| display.monitor(i))
        .map(|monitor| {
            // GDK counts in its own pixels; the window and the panel count in the screen's.
            let (a, s) = (monitor.workarea(), monitor.scale_factor());
            let area = Area { x: a.x() * s, y: a.y() * s, w: a.width() * s, h: a.height() * s };
            (Some(&monitor) == main.as_ref(), area)
        })
        .collect();
    main_first(found, || whole_screens(event_loop))
}

#[cfg(not(any(windows, target_os = "linux")))]
fn work_areas<T>(event_loop: &EventLoop<T>) -> Vec<Area> {
    whole_screens(event_loop)
}

fn whole_screens<T>(event_loop: &EventLoop<T>) -> Vec<Area> {
    let main = event_loop.primary_monitor();
    let found = event_loop
        .available_monitors()
        .map(|m| {
            let (at, size) = (m.position(), m.size());
            let area = Area { x: at.x, y: at.y, w: size.width as i32, h: size.height as i32 };
            (main.as_ref().map(|p| p.name() == m.name()).unwrap_or(false), area)
        })
        .collect();
    main_first(found, || vec![Area { x: 0, y: 0, w: 1280, h: 720 }])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_window_spans_every_screen() {
        let left = Area { x: -1920, y: 0, w: 1920, h: 1040 };
        let main = Area { x: 0, y: 0, w: 2560, h: 1400 };
        assert_eq!(span(&[main, left]), Area { x: -1920, y: 0, w: 4480, h: 1400 });
    }

    #[test]
    fn the_main_screen_comes_first() {
        let a = Area { x: 0, y: 0, w: 10, h: 10 };
        let b = Area { x: 10, y: 0, w: 10, h: 10 };
        assert_eq!(main_first(vec![(false, a), (true, b)], Vec::new), vec![b, a]);
        assert_eq!(main_first(Vec::new(), || vec![a]), vec![a]);
    }
}
