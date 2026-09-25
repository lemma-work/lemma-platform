// The WKWebView half of the app-alias proof. See README.md.
//
//   swift probe.swift <workspace url> <top-level app url>
//
// Loads the workspace page (which signs in and frames whatever `frame=` names),
// collects what the frame reported, then loads the app's canonical URL top
// level. One JSON object on stdout.

import AppKit
import WebKit

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    FileHandle.standardError.write("usage: probe <workspace url> <app url>\n".data(using: .utf8)!)
    exit(2)
}

final class Probe: NSObject, WKNavigationDelegate {
    let web: WKWebView
    let window: NSWindow
    var queue: [(String, String)]
    var results: [String: Any] = [:]

    init(steps: [(String, String)]) {
        queue = steps
        let configuration = WKWebViewConfiguration()
        // A fresh store every run: an inherited cookie would turn a regression
        // into a pass.
        configuration.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 800, height: 600), configuration: configuration)
        window = NSWindow(
            contentRect: NSRect(x: -3000, y: -3000, width: 800, height: 600),
            styleMask: [.borderless], backing: .buffered, defer: false)
        super.init()
        window.contentView = web
        window.orderFrontRegardless()
        web.navigationDelegate = self
    }

    func next() {
        guard !queue.isEmpty else {
            let data = try! JSONSerialization.data(withJSONObject: results, options: [.sortedKeys])
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write("\n".data(using: .utf8)!)
            exit(0)
        }
        let (name, url) = queue.removeFirst()
        web.evaluateJavaScript("window.__result = undefined") { _, _ in
            self.web.load(URLRequest(url: URL(string: url)!))
            self.poll(name: name, attempt: 0)
        }
    }

    func poll(name: String, attempt: Int) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            self.web.evaluateJavaScript("window.__result") { value, _ in
                if let value, !(value is NSNull) {
                    self.results[name] = value
                    self.next()
                } else if attempt > 75 {
                    self.results[name] = "timeout"
                    self.next()
                } else {
                    self.poll(name: name, attempt: attempt + 1)
                }
            }
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        FileHandle.standardError.write("navigation failed: \(error)\n".data(using: .utf8)!)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let probe = Probe(steps: [("workspace", arguments[1]), ("topLevelApp", arguments[2])])
DispatchQueue.main.async { probe.next() }
DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
    FileHandle.standardError.write("timed out\n".data(using: .utf8)!)
    exit(1)
}
app.run()
