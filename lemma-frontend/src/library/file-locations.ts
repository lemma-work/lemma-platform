export const fileLocations = {
    shared: { label: "Shared files", root: "/", description: "Files shared with everyone here." },
    personal: { label: "My files", root: "/me", description: "Your own files here." },
    skills: { label: "Skills", root: "/skills", description: "Reusable instructions and supporting files for your teammate." },
};
export type FileLocation = keyof typeof fileLocations;
export function inLocation(path: string, location: FileLocation) {
    const within = (root: string) => path === root || path.startsWith(root + "/");
    if (location === "personal") return within("/me");
    if (location === "skills") return within("/skills");
    return !within("/me") && !within("/skills");
}
export function parentFolder(path: string, location: FileLocation) {
    const root = fileLocations[location].root;
    const parent = path.replace(/\/+$/, "").slice(0, path.replace(/\/+$/, "").lastIndexOf("/")) || "/";
    return parent.length < root.length || !inLocation(parent, location) ? root : parent;
}
