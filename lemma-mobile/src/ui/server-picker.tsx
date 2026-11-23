import { useState } from "react";
import { TextInput, View } from "react-native";
import { PRESETS, THIS_COMPUTER, customServer, type Server } from "../session/server";
import { Icon } from "./icons";
import { Button, Press } from "./press";
import { Text } from "./text";
import { color, font, TOUCH } from "./tokens";

/** Which Lemma the app talks to: the hosted ones by name, or a custom
 *  address. Each keeps its own session, so switching back signs nobody
 *  out. Used on the sign-in screen and on the account screen. */

export function ServerPicker({ current, onPick }: { current: Server; onPick: (server: Server) => void }) {
    const isCustom = !PRESETS.some((one) => one.id === current.id);
    const [open, setOpen] = useState(isCustom);
    const [api, setApi] = useState(isCustom ? current.api : "");
    const [web, setWeb] = useState(isCustom ? current.web : "");
    const [problem, setProblem] = useState<string | null>(null);
    const field = { borderWidth: 1, borderColor: color.line2, borderRadius: 12, paddingHorizontal: 12, minHeight: TOUCH + 4, fontFamily: font.regular, fontSize: 16, color: color.ink } as const;
    const row = (last = false) => ({ flexDirection: "row" as const, alignItems: "center" as const, gap: 12, minHeight: 56, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line });
    const useCustom = () => {
        const next = customServer(api, web);
        if (!next) {
            setProblem("Give the API an address. The web app can be left out when the API is at api.something.");
            return;
        }
        setProblem(null);
        onPick(next);
    };
    return (
        <View>
            {PRESETS.map((server) => {
                const selected = server.id === current.id;
                return (
                    <Press key={server.id} label={`${server.label}${selected ? ", in use" : ""}`} accessibilityState={{ selected }} onPress={() => onPick(server)} style={row()}>
                        <View style={{ flex: 1 }}>
                            <Text size={16}>{server.label}</Text>
                            <Text size={13} tone={color.ink3}>{server.api.replace(/^https?:\/\//, "")}</Text>
                        </View>
                        {selected ? <Icon name="check" size={18} width={2} /> : null}
                    </Press>
                );
            })}
            <Press label={`Custom${isCustom ? `, ${current.label}, in use` : ""}`} accessibilityState={{ selected: isCustom, expanded: open }} onPress={() => setOpen((was) => !was)} style={row(!open)}>
                <View style={{ flex: 1 }}>
                    <Text size={16}>Custom</Text>
                    <Text size={13} tone={color.ink3} numberOfLines={1}>{isCustom ? current.label : "Another deployment, or a dev stack"}</Text>
                </View>
                {isCustom ? <Icon name="check" size={18} width={2} /> : <Icon name={open ? "down" : "forward"} size={16} tone={color.ink3} />}
            </Press>
            {open ? (
                <View style={{ gap: 10, paddingTop: 10 }}>
                    <TextInput accessibilityLabel="API address" placeholder="API, e.g. api.example.com" placeholderTextColor={color.ink3} autoCapitalize="none" autoCorrect={false} keyboardType="url" value={api} onChangeText={setApi} style={field} />
                    <TextInput accessibilityLabel="Web app address" placeholder="Web app, if not at the API's own domain" placeholderTextColor={color.ink3} autoCapitalize="none" autoCorrect={false} keyboardType="url" value={web} onChangeText={setWeb} style={field} />
                    <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>A bare port is this computer ({THIS_COMPUTER}). A local address uses http, anything else https.</Text>
                    {problem ? <Text size={13.5} tone={color.amber}>{problem}</Text> : null}
                    <View style={{ flexDirection: "row" }}>
                        <Button title="Use this server" onPress={useCustom} />
                    </View>
                </View>
            ) : null}
        </View>
    );
}
