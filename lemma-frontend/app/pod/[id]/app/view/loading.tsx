/**
 * Deliberately empty. This route only anchors the URL; the live surface is owned
 * by the pod shell's keep-alive host above the router, so any skeleton here
 * would paint a second page on top of a surface that is already running.
 */
export default function AppViewLoading() {
    return <div className="h-full w-full" />;
}
