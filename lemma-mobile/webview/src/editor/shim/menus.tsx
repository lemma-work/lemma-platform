import { forwardRef, type ComponentProps } from "react";
import { BubbleMenu as Bubble, FloatingMenu } from "@tiptap/react/menus";

/** TipTap's menus as the web uses them, with one change for a phone: the
 *  selection toolbar sits under the selection. Above it is where the phone
 *  puts its own Cut, Copy, Paste, and the two would cover each other. */

type Props = ComponentProps<typeof Bubble>;

export const BubbleMenu = forwardRef<HTMLDivElement, Props>(function PhoneBubbleMenu(props, ref) {
    const options = { ...props.options, placement: "bottom" as const, offset: 14 };
    return <Bubble {...props} ref={ref} options={options} />;
});

export { FloatingMenu };
