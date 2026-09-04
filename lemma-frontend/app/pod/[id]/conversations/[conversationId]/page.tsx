/**
 * The conversation surface is rendered by `../layout.tsx`, one segment up.
 *
 * It has to live above this one: Next keys a route by its concrete path, so
 * `/conversations/new` and `/conversations/<id>` are different pages, and the
 * navigation between them — which every first message performs — would unmount
 * the surface and build it again mid-send. A layout above the dynamic segment
 * survives that navigation while still seeing the new id.
 *
 * So this page draws nothing. It exists to make the route addressable.
 */
export default function PodConversationPage() {
    return null;
}
