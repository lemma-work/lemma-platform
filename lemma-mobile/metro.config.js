/* The web's code is part of the app's bundle where it lives (`web-imports.js`). */

const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");
const { WEB, SHIMS } = require("./web-imports");

const config = getDefaultConfig(__dirname);

config.watchFolders = [...(config.watchFolders ?? []), WEB];

const fromApp = path.join(__dirname, "package.json");
const inWeb = (file) => file.startsWith(WEB + path.sep);
const shimmed = new Map(Object.entries(SHIMS).map(([web, own]) => [path.join(WEB, web), path.join(__dirname, own)]));

function resolve(context, name, platform) {
    if (name.startsWith("@web/")) return context.resolveRequest(context, path.join(WEB, name.slice("@web/".length)), platform);
    if (inWeb(context.originModulePath)) {
        if (name.startsWith("@/")) return context.resolveRequest(context, path.join(WEB, name.slice("@/".length)), platform);
        /* A package: from the app's node_modules, as if the app asked. */
        if (!name.startsWith(".") && !path.isAbsolute(name)) return context.resolveRequest({ ...context, originModulePath: fromApp }, name, platform);
    }
    return context.resolveRequest(context, name, platform);
}

config.resolver.resolveRequest = (context, name, platform) => {
    const found = resolve(context, name, platform);
    const own = found.type === "sourceFile" ? shimmed.get(found.filePath) : undefined;
    return own ? { type: "sourceFile", filePath: own } : found;
};

module.exports = config;
