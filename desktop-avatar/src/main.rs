//! The Code-AI avatar loose on the desktop, for Windows and Linux; macOS has
//! its own in src/code_ai/desktop_avatar/cocoa.py.
//!
//!     code-ai-avatar <page.html> [<data dir>]
//!
//! One transparent, borderless, always-on-top window over the work area,
//! showing the page it is given. Clicks go through it everywhere but on the
//! face. Each stdin line is a JSON object for the page's `desk.receive`; each
//! `out:<json>` the page posts comes back as a stdout line. The end of stdin
//! closes it, so it never outlives what started it. The web view keeps its
//! profile in the data dir: beside the executable, where it would go by
//! default, may be a one-file build's temporary folder or not writable at all.

#![cfg_attr(windows, windows_subsystem = "windows")]

use std::error::Error;
use std::io::{BufRead, Write};
use std::time::{Duration, Instant};

use tao::{
    dpi::{Position, Size},
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
    // Wayland lets no window keep itself on top, put itself somewhere or see
    // the pointer outside it. XWayland does all three.
    #[cfg(target_os = "linux")]
    if std::env::var_os("GDK_BACKEND").is_none() && std::env::var_os("DISPLAY").is_some() {
        std::env::set_var("GDK_BACKEND", "x11");
    }
    if let Err(err) = run(html, data) {
        eprintln!("{err}");
        std::process::exit(1);
    }
}

fn run(html: String, data: Option<std::path::PathBuf>) -> Result<(), Box<dyn Error>> {
    let event_loop = EventLoopBuilder::<Ask>::with_user_event().build();
    // After the loop: it is what makes the process DPI-aware, and starts GTK.
    let (position, size) = work_area(&event_loop);
    let builder = WindowBuilder::new()
        .with_title("Code-AI")
        .with_decorations(false)
        .with_transparent(true)
        .with_always_on_top(true)
        .with_focused(false)
        .with_resizable(false)
        .with_position(position)
        .with_inner_size(size);
    #[cfg(windows)]
    let builder = {
        use tao::platform::windows::WindowBuilderExtWindows;
        builder.with_skip_taskbar(true).with_undecorated_shadow(false)
    };
    #[cfg(target_os = "linux")]
    let builder = {
        use tao::platform::unix::WindowBuilderExtUnix;
        builder.with_skip_taskbar(true)
    };
    let window = builder.build(&event_loop)?;
    // After the loop too: on Linux it is a WebKitGTK object, and GTK must be up.
    let mut context = WebContext::new(data);
    let webview = webview(&window, html, &event_loop, &mut context)?;

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
                // Where this window is, in physical pixels: the panel says where its
                // logo is on the screen in those, and the page sits the avatar there.
                let at = window.inner_position().unwrap_or_default();
                let scale = window.scale_factor();
                let _ = webview.evaluate_script(&format!("window.desk&&desk.frame({},{},{scale})", at.x, at.y));
                for line in waiting.drain(..) {
                    deliver(&webview, &line);
                }
            }
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

/// The main screen less the taskbar.
#[cfg(windows)]
fn work_area<T>(_: &EventLoop<T>) -> (Position, Size) {
    use tao::dpi::{PhysicalPosition, PhysicalSize};
    use windows_sys::Win32::{
        Foundation::RECT,
        UI::WindowsAndMessaging::{SystemParametersInfoW, SPI_GETWORKAREA},
    };
    let mut r = RECT { left: 0, top: 0, right: 0, bottom: 0 };
    unsafe { SystemParametersInfoW(SPI_GETWORKAREA, 0, &mut r as *mut RECT as *mut _, 0) };
    (
        PhysicalPosition::new(r.left, r.top).into(),
        PhysicalSize::new((r.right - r.left) as u32, (r.bottom - r.top) as u32).into(),
    )
}

/// The main screen less the panels.
#[cfg(target_os = "linux")]
fn work_area<T>(event_loop: &EventLoop<T>) -> (Position, Size) {
    use gtk::prelude::MonitorExt;
    use tao::dpi::{LogicalPosition, LogicalSize};
    match gtk::gdk::Display::default().and_then(|d| d.primary_monitor().or_else(|| d.monitor(0))) {
        Some(monitor) => {
            let a = monitor.workarea();
            (LogicalPosition::new(a.x(), a.y()).into(), LogicalSize::new(a.width() as u32, a.height() as u32).into())
        }
        None => whole_screen(event_loop),
    }
}

#[cfg(not(any(windows, target_os = "linux")))]
fn work_area<T>(event_loop: &EventLoop<T>) -> (Position, Size) {
    whole_screen(event_loop)
}

#[allow(dead_code)]
fn whole_screen<T>(event_loop: &EventLoop<T>) -> (Position, Size) {
    use tao::dpi::{PhysicalPosition, PhysicalSize};
    match event_loop.primary_monitor() {
        Some(m) => (m.position().into(), m.size().into()),
        None => (PhysicalPosition::new(0, 0).into(), PhysicalSize::new(1280u32, 720u32).into()),
    }
}
