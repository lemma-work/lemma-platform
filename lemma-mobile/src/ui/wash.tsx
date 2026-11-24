import { StyleSheet, View, useWindowDimensions } from "react-native";
import Svg, { Defs, RadialGradient, Rect, Stop } from "react-native-svg";
import { color } from "./tokens";

/** The light the web's sidebar is lit with (`.ox-side`): the brand's
 *  lavender washed in from the top left, a cool blue on the right. Home and
 *  the way in sit in it, so the phone opens in the same light the web does. */
export function Wash({ height = 460 }: { height?: number }) {
    const { width } = useWindowDimensions();
    return (
        <View pointerEvents="none" style={[StyleSheet.absoluteFill, { height }]}>
            <Svg width={width} height={height}>
                <Defs>
                    <RadialGradient id="lav" cx="0" cy="0" rx={width * 1.5} ry={height * 0.62} gradientUnits="userSpaceOnUse" fx="0" fy="0">
                        <Stop offset="0" stopColor={color.tint} stopOpacity="1" />
                        <Stop offset="0.7" stopColor={color.tint} stopOpacity="0" />
                    </RadialGradient>
                    <RadialGradient id="blue" cx={width} cy={height * 0.22} rx={width * 0.8} ry={height * 0.34} gradientUnits="userSpaceOnUse" fx={width} fy={height * 0.22}>
                        <Stop offset="0" stopColor={color.tint2} stopOpacity="0.7" />
                        <Stop offset="0.75" stopColor={color.tint2} stopOpacity="0" />
                    </RadialGradient>
                </Defs>
                <Rect x="0" y="0" width={width} height={height} fill="url(#lav)" />
                <Rect x="0" y="0" width={width} height={height} fill="url(#blue)" />
            </Svg>
        </View>
    );
}
