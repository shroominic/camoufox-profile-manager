// Native Studio identity and WKWebView host. Python is a private backend only.
import AppKit
import Darwin
import WebKit

struct RuntimeConfiguration: Decodable {
    let version: Int
    let root: String
    let python: String
    let database: String
    let routineCatalog: String
    let scriptImportPaths: [String]
    let dataDirectory: String?
}

@main final class StudioHost: NSObject, NSApplicationDelegate, NSWindowDelegate,
    WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private let backend = Process()
    private let control = Pipe()
    private let startup = Pipe()
    private var startupBytes = Data()
    private var window: NSWindow?
    private var webView: WKWebView?
    private var origin: URL?
    private var stopping = false
    private var reportedFailure = false
    private var startupNotice: NSAlert?

    static func main() {
        signal(SIGPIPE, SIG_IGN)
        let app = NSApplication.shared
        let host = StudioHost()
        app.delegate = host
        app.setActivationPolicy(.regular)
        app.run()
        withExtendedLifetime(host) {}
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenu()
        showStartingWindow()
        do {
            guard let configURL = Bundle.main.url(forResource: "studio-runtime", withExtension: "json") else {
                throw CocoaError(.fileNoSuchFile)
            }
            let config = try JSONDecoder().decode(RuntimeConfiguration.self, from: Data(contentsOf: configURL))
            guard config.version == 1, config.root.hasPrefix("/"), config.python.hasPrefix("/"),
                  config.database.hasPrefix("/"), config.routineCatalog.hasPrefix("/"),
                  config.scriptImportPaths.allSatisfy({ $0.hasPrefix("/") }) else {
                throw CocoaError(.coderInvalidValue)
            }
            backend.executableURL = URL(fileURLWithPath: config.python)
            backend.currentDirectoryURL = URL(fileURLWithPath: config.root, isDirectory: true)
            backend.arguments = ["-m", "camoufox_pm.studio_backend"]
            if let dataDirectory = config.dataDirectory {
                guard dataDirectory.hasPrefix("/") else { throw CocoaError(.coderInvalidValue) }
                backend.arguments!.append(contentsOf: ["--data-dir", dataDirectory])
            }
            var environment = ProcessInfo.processInfo.environment
            environment.removeValue(forKey: "CPM_SECRET_KEY")
            environment.removeValue(forKey: "CPM_WEBUI_DIR")
            environment["PYTHONPATH"] = config.root + "/src"
            environment["PYTHONUNBUFFERED"] = "1"
            environment["CPM_DB_PATH"] = config.database
            environment["CPM_ROUTINE_CATALOG_PATH"] = config.routineCatalog
            environment["CPM_SCRIPT_IMPORT_PATHS"] = String(
                decoding: try JSONEncoder().encode(config.scriptImportPaths), as: UTF8.self)
            backend.environment = environment
            backend.standardInput = control
            backend.standardOutput = startup
            backend.standardError = FileHandle.nullDevice
            startup.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                DispatchQueue.main.async { self?.receive(data) }
            }
            backend.terminationHandler = { [weak self] process in
                DispatchQueue.main.async { self?.backendExited(process.terminationStatus) }
            }
            try backend.run()
            // The host owns the write end; a host exit closes it and requests
            // backend cleanup even when no Quit event can be delivered.
            control.fileHandleForReading.closeFile()
            startup.fileHandleForWriting.closeFile()
            DispatchQueue.main.asyncAfter(deadline: .now() + 90) { [weak self] in
                guard let self, self.origin == nil, self.backend.isRunning, !self.stopping,
                      let window = self.window else { return }
                let alert = NSAlert()
                alert.messageText = "Studio is still starting"
                alert.informativeText = "Opening your saved profiles is taking longer than expected. You can keep waiting or close Studio."
                alert.addButton(withTitle: "Keep waiting")
                alert.addButton(withTitle: "Close Studio")
                self.startupNotice = alert
                // A slow backend can finish while this sheet is visible. Do
                // not block its readiness callback behind a modal run loop.
                alert.beginSheetModal(for: window) { [weak self] response in
                    guard let self else { return }
                    self.startupNotice = nil
                    if response == .alertSecondButtonReturn && self.origin == nil { self.requestStop() }
                }
            }
        } catch {
            report("Studio could not start its local backend. Check the installed app and Python environment.")
            NSApp.terminate(nil)
        }
    }

    private func showStartingWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1280, height: 800)
        let hostWindow = NSWindow(contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        hostWindow.title = "Camoufox Studio · Starting"
        hostWindow.delegate = self
        let content = NSView(frame: frame)
        let label = NSTextField(labelWithString: "Opening your saved profiles…")
        label.font = .systemFont(ofSize: 18)
        label.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(label)
        let progress = NSProgressIndicator()
        progress.style = .spinning
        progress.isIndeterminate = true
        progress.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(progress)
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: content.centerYAnchor),
            progress.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            progress.topAnchor.constraint(equalTo: label.bottomAnchor, constant: 18),
        ])
        progress.startAnimation(nil)
        hostWindow.contentView = content
        hostWindow.center()
        window = hostWindow
        hostWindow.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func receive(_ data: Data) {
        guard origin == nil else { return }
        if data.isEmpty { startup.fileHandleForReading.readabilityHandler = nil; return }
        startupBytes.append(data)
        guard startupBytes.count <= 4096 else {
            report("Studio returned an invalid startup response."); requestStop(); return
        }
        guard let newline = startupBytes.firstIndex(of: 10) else { return }
        startup.fileHandleForReading.readabilityHandler = nil
        do {
            guard let record = try JSONSerialization.jsonObject(with: startupBytes[..<newline]) as? [String: Any],
                  record["protocol"] as? Int == 1 else { throw CocoaError(.coderInvalidValue) }
            if let error = record["error"] as? String {
                report(error == "backend_already_running"
                    ? "Close Camoufox Profile Manager or the other Camoufox Studio window, then open Studio again. Both apps use the same saved profiles."
                    : "Studio could not start its local backend. Check its local studio.log.")
                requestStop()
                return
            }
            guard
                  let address = record["url"] as? String, let url = URL(string: address),
                  url.scheme == "http", url.host == "127.0.0.1", let port = url.port,
                  (1...65535).contains(port), url.path == "/", url.user == nil,
                  url.password == nil, url.query == nil, url.fragment == nil else {
                throw CocoaError(.coderInvalidValue)
            }
            origin = url
            if let notice = startupNotice, let window {
                window.endSheet(notice.window, returnCode: .abort)
                notice.window.orderOut(nil)
                startupNotice = nil
            }
            guard !stopping else { return }
            let view = WKWebView(frame: .zero)
            view.navigationDelegate = self
            view.uiDelegate = self
            guard let hostWindow = window else { throw CocoaError(.coderInvalidValue) }
            hostWindow.title = record["title"] as? String ?? "Camoufox Studio"
            hostWindow.delegate = self
            hostWindow.contentView = view
            webView = view
            view.load(URLRequest(url: url))
            hostWindow.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        } catch {
            report("Studio could not open its local interface. Check its local studio.log.")
            requestStop()
        }
    }

    private func report(_ message: String) {
        guard !reportedFailure else { return }
        reportedFailure = true
        let alert = NSAlert()
        alert.messageText = "Camoufox Studio"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }

    private func requestStop() {
        guard backend.isRunning else { NSApp.reply(toApplicationShouldTerminate: true); NSApp.terminate(nil); return }
        guard !stopping else { return }
        stopping = true
        window?.title = "Camoufox Studio · Stopping"
        try? control.fileHandleForWriting.write(contentsOf: Data("shutdown\n".utf8))
        try? control.fileHandleForWriting.close()
        DispatchQueue.main.asyncAfter(deadline: .now() + 22) { [weak self] in
            guard let self, self.backend.isRunning else { return }
            // Never leave a live backend holding the shared state lock behind
            // a successful native Quit. An unclean shutdown remains visible.
            self.report("Studio is still closing its browser sessions. The app will remain open until cleanup finishes.")
        }
    }

    private func backendExited(_ status: Int32) {
        startup.fileHandleForReading.readabilityHandler = nil
        if status != 0 && !reportedFailure {
            report("Studio's backend stopped. Check its local studio.log before reopening the app.")
        }
        stopping = true
        NSApp.reply(toApplicationShouldTerminate: true)
        NSApp.terminate(nil)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard backend.isRunning else { return .terminateNow }
        requestStop()
        return .terminateLater
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { requestStop(); return false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window?.makeKeyAndOrderFront(nil)
        return true
    }

    private func installMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Camoufox Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem()
        menu.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"),
                                    ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                                    ("Select All", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = edit
        let viewItem = NSMenuItem()
        menu.addItem(viewItem)
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r").target = self
        viewItem.submenu = view
        NSApp.mainMenu = menu
    }
    @objc private func reload() { webView?.reloadFromOrigin() }

    private func sameOrigin(_ url: URL) -> Bool {
        guard let origin else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port
            && url.user == nil && url.password == nil
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if sameOrigin(url) || (url.scheme == "blob" && url.absoluteString.hasPrefix("blob:" + origin!.absoluteString)) {
            decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
        } else {
            if navigationAction.navigationType == .linkActivated && ["http", "https"].contains(url.scheme ?? "") {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, sameOrigin(url) { webView.load(navigationAction.request) }
        return nil
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = navigationResponse.response.url,
              sameOrigin(url) || url.scheme == "blob" else { decisionHandler(.cancel); return }
        let attachment = (navigationResponse.response as? HTTPURLResponse)?
            .value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") ?? false
        decisionHandler(!navigationResponse.canShowMIMEType || attachment ? .download : .allow)
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.begin { response in completionHandler(response == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = "Camoufox Studio"; alert.informativeText = message
        alert.runModal(); completionHandler()
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = "Camoufox Studio"; alert.informativeText = message
        alert.addButton(withTitle: "Continue"); alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.begin { response in completionHandler(response == .OK ? panel.url : nil) }
    }
}
