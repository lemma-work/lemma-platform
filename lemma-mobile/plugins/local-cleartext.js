/**
 * Plain HTTP to a dev stack on this computer, and nowhere else.
 *
 * Android refuses cleartext by default, which is right for lemma.work and
 * wrong for `http://10.0.2.2:8000`, the emulator's name for the machine
 * running it. This writes a network security config that allows cleartext
 * for the loopback names a dev stack answers on and keeps HTTPS everywhere
 * else, rather than the blanket `usesCleartextTraffic`.
 *
 * Only debug builds get the exception: it lives in the `debug` source set,
 * which Gradle lays over `main` for that variant alone. A release build, the
 * one a store gets, carries HTTPS only, so nothing on the phone listening on
 * loopback can be spoken to in the clear.
 */
const fs = require("fs");
const path = require("path");
const { withAndroidManifest, withDangerousMod } = require("expo/config-plugins");

const HOSTS = ["10.0.2.2", "localhost", "127.0.0.1"];

const DEBUG_XML = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="false" />
    <domain-config cleartextTrafficPermitted="true">
${HOSTS.map((host) => `        <domain includeSubdomains="true">${host}</domain>`).join("\n")}
    </domain-config>
</network-security-config>
`;

const RELEASE_XML = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="false" />
</network-security-config>
`;

module.exports = function withLocalCleartext(config) {
    config = withDangerousMod(config, [
        "android",
        async (inner) => {
            for (const [variant, xml] of [["main", RELEASE_XML], ["debug", DEBUG_XML]]) {
                const dir = path.join(inner.modRequest.platformProjectRoot, "app", "src", variant, "res", "xml");
                fs.mkdirSync(dir, { recursive: true });
                fs.writeFileSync(path.join(dir, "network_security_config.xml"), xml);
            }
            return inner;
        },
    ]);
    return withAndroidManifest(config, (inner) => {
        const application = inner.modResults.manifest.application?.[0];
        if (application) application.$["android:networkSecurityConfig"] = "@xml/network_security_config";
        return inner;
    });
};
