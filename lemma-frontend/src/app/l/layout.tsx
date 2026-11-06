import { Workspace } from "./workspace";
// Persistent layout retains queries, conversations and open app iframes across pod navigation.
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
    return <><Workspace />{children}</>;
}
