/**
 * Plain HTTP to a dev stack on this computer, and nowhere else.
 *
 * Android refuses cleartext by default, which is right for lemma.work and
 * wrong for `http://10.0.2.2:8000`, the emulator's name for the machine
 * running it. This writes a network security config that allows cleartext
 * for the loopback names a dev stack answers on and keeps HTTPS everywhere
 * else, rather than the blanket `usesCleartextTraffic`.
 */
const fs = require("fs");
const path = require("path");
const { withAndroidManifest, withDangerousMod } = require("expo/config-plugins");

const HOSTS = ["10.0.2.2", "localhost", "127.0.0.1"];

const XML = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="false" />
    <domain-config cleartextTrafficPermitted="true">
${HOSTS.map((host) => `        <domain includeSubdomains="true">${host}</domain>`).join("\n")}
    </domain-config>
</network-security-config>
`;

module.exports = function withLocalCleartext(config) {
    config = withDangerousMod(config, [
        "android",
        async (inner) => {
            const dir = path.join(inner.modRequest.platformProjectRoot, "app", "src", "main", "res", "xml");
            fs.mkdirSync(dir, { recursive: true });
            fs.writeFileSync(path.join(dir, "network_security_config.xml"), XML);
            return inner;
        },
    ]);
    return withAndroidManifest(config, (inner) => {
        const application = inner.modResults.manifest.application?.[0];
        if (application) application.$["android:networkSecurityConfig"] = "@xml/network_security_config";
        return inner;
    });
};
