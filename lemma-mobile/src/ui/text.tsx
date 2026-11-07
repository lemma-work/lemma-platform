import { Text as RNText, type TextProps, type TextStyle } from "react-native";
import { color, font } from "./tokens";

type Weight = "regular" | "medium";

/** Text in the app's two faces. A custom font on Android ignores
 *  `fontWeight` and falls back to the system face, so weight is a family. */
export function Text({ weight = "regular", size = 15.5, tone = color.ink, style, ...rest }: TextProps & {
    weight?: Weight;
    size?: number;
    tone?: string;
}) {
    return (
        <RNText
            {...rest}
            allowFontScaling
            style={[{ fontFamily: weight === "medium" ? font.medium : font.regular, fontSize: size, color: tone }, style]}
        />
    );
}

/** A number: Inter with tabular figures, so a count that changes does not
 *  make the line beside it jump. */
export function Num({ weight = "regular", size, tone, style, ...rest }: TextProps & {
    weight?: Weight;
    size?: number;
    tone?: string;
}) {
    const face: TextStyle = {
        fontFamily: weight === "medium" ? font.numMedium : font.num,
        fontVariant: ["tabular-nums"],
        ...(size !== undefined ? { fontSize: size } : {}),
        ...(tone !== undefined ? { color: tone } : {}),
    };
    return <RNText {...rest} style={[face, style]} />;
}

/** Split words around the numbers in them, so "Refund $240 to Northwind"
 *  sets $240 in Inter without the caller marking it. */
export function Counted({ children, size = 15.5, tone = color.ink, weight = "regular", numberOfLines, style }: {
    children: string;
    size?: number;
    tone?: string;
    weight?: Weight;
    numberOfLines?: number;
    style?: TextStyle;
}) {
    const parts = children.split(/([$€£]?\d[\d,.:]*%?)/g).filter((part) => part !== "");
    return (
        <Text size={size} tone={tone} weight={weight} numberOfLines={numberOfLines} style={style}>
            {parts.map((part, index) => /\d/.test(part)
                ? <Num key={index} weight={weight} size={size} tone={tone}>{part}</Num>
                : part)}
        </Text>
    );
}
