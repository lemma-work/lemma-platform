// Does a pod app, loaded in the engine Lemma actually ships, have a session?
//
// This has to be WKWebView and nothing else. The bug it guards against --
// every pod app rendering signed out -- reproduces *only* here: Chromium sends
// the cookie in exactly this arrangement, and a Playwright or curl-based test
// would have reported the broken build as fixed. `localhost` is not in the
// Public Suffix List, so WebKit cannot derive a registrable domain and treats
// `<slug>.apps.lemma.localhost` and `app.lemma.localhost` as separate sites;
// anything cross-site there is third-party and ITP drops the cookie.
//
// The session is established the way a person establishes one -- a real sign-in
// from the frontend origin, so the cookie is written by the browser with the
// attributes the server actually sent. Injecting cookies into WKHTTPCookieStore
// would skip the half most likely to be wrong.
//
//   swift pod_app_session_probe.swift <config.json>
//
// Config: { frontendUrl, apiUrl, appUrl, email, password }
// Prints one JSON object to stdout. Exit 0 only if the app is signed in.

import Foundation
import WebKit

struct Config: Decodable {
    let frontendUrl: String
    let apiUrl: String
    let appUrl: String
    let email: String
    let password: String
    /// "toplevel" (default) or "embedded".
    ///
    /// Both matter, and they fail independently. A pod app opened in its own
    /// window is first-party and works; the same app in an iframe on the
    /// workspace is third-party, and WebKit gives a third-party frame no
    /// storage at all -- no cookie stored, no `document.cookie`, no storage
    /// access. That is decided against the *top* frame, so nothing the app or
    /// the API does can change it. Testing only the top-level case is how this
    /// shipped broken twice.
    let mode: String?
}

// Every failure mode gets a name, because "it did not work" against a stack
// this deep costs an hour to narrow down by hand.
enum Stage: String {
    case loadingFrontend = "loading the frontend"
    case signingIn = "signing in"
    case loadingApp = "loading the app"
    case callingApi = "calling the API from the app"
    case embeddingApp = "embedding the app in the workspace"
}

let arguments = CommandLine.arguments
guard arguments.count == 2 else {
    FileHandle.standardError.write("usage: pod_app_session_probe <config.json>\n".data(using: .utf8)!)
    exit(2)
}
let config: Config
do {
    config = try JSONDecoder().decode(Config.self, from: Data(contentsOf: URL(fileURLWithPath: arguments[1])))
} catch {
    FileHandle.standardError.write("unreadable config: \(error)\n".data(using: .utf8)!)
    exit(2)
}

func jsonString(_ value: String) -> String {
    let data = try! JSONSerialization.data(withJSONObject: [value], options: [])
    var text = String(data: data, encoding: .utf8)!
    text.removeFirst()  // [
    text.removeLast()   // ]
    return text
}

/// Sign in against SuperTokens the way the web client does, from the frontend
/// origin. `credentials: "include"` so the browser stores what it is sent.
let signInScript = """
(async () => {
  try {
    const response = await fetch(\(jsonString(config.apiUrl)) + "/st/auth/signin", {
      method: "POST",
      credentials: "include",
      // st-auth-mode: cookie is what the browser SDK sends, and it is what
      // decides whether the server replies with Set-Cookie or with header
      // tokens. Without it SuperTokens defaults to header transfer, the browser
      // stores no session, and the app's call below returns 401 -- which looks
      // exactly like the bug this probe exists to detect.
      headers: {
        "Content-Type": "application/json",
        "rid": "emailpassword",
        "st-auth-mode": "cookie",
      },
      body: JSON.stringify({ formFields: [
        { id: "email", value: \(jsonString(config.email)) },
        { id: "password", value: \(jsonString(config.password)) },
      ]}),
    });
    const body = await response.json();
    window.__signIn = { httpStatus: response.status, status: body.status || null };
  } catch (error) {
    window.__signIn = { error: String(error) };
  }
})();
"""

/// The embedded assertion: the workspace frames the app, exactly as
/// `AppFrameHost` does.
///
/// The frame is cross-origin, so this side cannot reach into it -- which is the
/// whole point, and why the app has to report for itself. The published test app
/// posts its own `/users/me` result to `parent`, and this collects it.
///
/// A silent timeout is the expected shape of the failure: a frame with no
/// storage cannot even fail loudly.
let embedScript = """
(async () => {
  window.__embed = null;
  const appUrl = \(jsonString(config.appUrl));
  const expected = new URL(appUrl).origin;
  window.addEventListener("message", (event) => {
    if (event.origin !== expected) return;
    if (event.data && event.data.kind === "lemma-e2e-session") {
      window.__embed = event.data;
    }
  });
  const frame = document.createElement("iframe");
  frame.src = appUrl;
  frame.style.width = "800px";
  frame.style.height = "600px";
  document.body.appendChild(frame);
})();
"""

/// The assertion. Runs *inside the app's own document*, so it exercises exactly
/// what a pod app does on load: a credentialed call to the API through the
/// app's own origin.
///
/// The URL is read from the injected config rather than hardcoded, so if the
/// serving path ever stops handing apps an apiUrl this fails here instead of
/// passing against a value the test invented.
let apiCallScript = """
(async () => {
  try {
    const injected = window.__LEMMA_CONFIG__ || {};
    if (!injected.apiUrl) {
      window.__probe = { error: "the served page carries no __LEMMA_CONFIG__.apiUrl" };
      return;
    }
    const response = await fetch(injected.apiUrl + "/users/me", { credentials: "include" });
    let email = null;
    if (response.ok) {
      const body = await response.json();
      email = body.email || null;
    }
    window.__probe = {
      apiUrl: injected.apiUrl,
      status: response.status,
      email: email,
      origin: location.origin,
    };
  } catch (error) {
    window.__probe = { error: String(error) };
  }
})();
"""

final class Probe: NSObject, WKNavigationDelegate {
    let web: WKWebView
    var stage: Stage = .loadingFrontend

    override init() {
        let configuration = WKWebViewConfiguration()
        // Non-persistent so a run never inherits (or leaves behind) a session
        // from a previous one. A stale cookie here would turn a regression into
        // a pass.
        configuration.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        web.navigationDelegate = self
    }

    func start() {
        web.load(URLRequest(url: URL(string: config.frontendUrl)!))
    }

    func fail(_ message: String) -> Never {
        emit(["ok": false, "stage": stage.rawValue, "error": message])
    }

    func emit(_ payload: [String: Any]) -> Never {
        let data = try! JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write("\n".data(using: .utf8)!)
        exit((payload["ok"] as? Bool) == true ? 0 : 1)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        switch stage {
        case .loadingFrontend:
            stage = .signingIn
            webView.evaluateJavaScript(signInScript) { _, _ in
                self.pollFor("window.__signIn") { result in
                    guard let result = result as? [String: Any] else {
                        self.fail("sign-in never answered")
                    }
                    if let error = result["error"] as? String {
                        self.fail("sign-in threw: \(error)")
                    }
                    let status = result["status"] as? String
                    guard status == "OK" else {
                        self.fail("sign-in refused: \(status ?? "no status") "
                                  + "(HTTP \(result["httpStatus"] as? Int ?? 0))")
                    }
                    guard (config.mode ?? "toplevel") == "embedded" else {
                        self.stage = .loadingApp
                        webView.load(URLRequest(url: URL(string: config.appUrl)!))
                        return
                    }
                    // Stay on the workspace origin and frame the app from here,
                    // which is what makes the frame third-party.
                    self.stage = .embeddingApp
                    webView.evaluateJavaScript(embedScript) { _, _ in
                        self.pollFor("window.__embed") { result in
                            guard let result = result as? [String: Any] else {
                                self.fail("the embedded app never reported")
                            }
                            let status = result["status"] as? Int ?? 0
                            let email = result["email"] as? String
                            self.emit([
                                "ok": status == 200 && email == config.email,
                                "status": status,
                                "email": email ?? NSNull(),
                                "expectedEmail": config.email,
                                "mode": "embedded",
                            ])
                        }
                    }
                }
            }
        case .loadingApp:
            stage = .callingApi
            webView.evaluateJavaScript(apiCallScript) { _, _ in
                self.pollFor("window.__probe") { result in
                    guard let result = result as? [String: Any] else {
                        self.fail("the app never answered")
                    }
                    if let error = result["error"] as? String {
                        self.fail(error)
                    }
                    let status = result["status"] as? Int ?? 0
                    let email = result["email"] as? String
                    self.emit([
                        // 401 here is the shipped bug: the page loads, and its
                        // first authenticated call is rejected.
                        "ok": status == 200 && email == config.email,
                        "status": status,
                        "email": email ?? NSNull(),
                        "expectedEmail": config.email,
                        "apiUrl": result["apiUrl"] as? String ?? NSNull(),
                        "appOrigin": result["origin"] as? String ?? NSNull(),
                    ])
                }
            }
        default:
            break
        }
    }

    /// The scripts above are async, so the value appears after `evaluateJavaScript`
    /// returns. Polled rather than slept on: a fixed sleep is either flaky or slow.
    func pollFor(_ expression: String, attempt: Int = 0, then: @escaping (Any?) -> Void) {
        guard attempt < 100 else { fail("\(expression) never appeared") }
        web.evaluateJavaScript(expression) { value, _ in
            if let value, !(value is NSNull) {
                then(value)
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    self.pollFor(expression, attempt: attempt + 1, then: then)
                }
            }
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail("navigation failed: \(error.localizedDescription)")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail("navigation failed: \(error.localizedDescription)")
    }
}

let probe = Probe()
probe.start()
DispatchQueue.main.asyncAfter(deadline: .now() + 120) {
    probe.fail("timed out")
}
RunLoop.main.run()
