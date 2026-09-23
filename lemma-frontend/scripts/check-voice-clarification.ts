/** Synthetic voice protocol check: no microphone and no Lemma execution. */
import { WebSocket } from "ws";
import { instructionFor } from "../src/call/voice-instructions.ts";
import { VoiceEvents } from "../src/call/voice-events.ts";
const socket = new WebSocket("ws://localhost:3000/api/voice");
const queue = new VoiceEvents();
queue.receive({ id: "test", conversationId: null, kind: "clarify", speak: true, text: "The caller said 'update that report', but two reports were discussed. Ask which report they mean." });
let stage = 0;
let passed = false;
const audio = [0, 0];
const words = ["", ""];
const timer = setTimeout(() => { console.error(JSON.stringify({ error: "Voice response timed out", stage, audio, words })); process.exitCode = 1; socket.close(); }, 25000);
const send = (type: string, payload: unknown) => socket.send(JSON.stringify({ type, payload }));
socket.on("open", () => send("start", { systemInstruction: instructionFor("Researcher", "Test pod", "A synthetic test conversation.") }));
socket.on("message", data => {
    const message = JSON.parse(data.toString());
    if (message.type === "error") { console.error(message.message); process.exitCode = 1; clearTimeout(timer); socket.close(); }
    if (message.type === "ready") {
        send("content", { turns: "Quiet context: two reports were discussed. No work is currently running.", turnComplete: false });
        send("content", { turns: queue.take(), turnComplete: true });
    }
    const content = message.payload?.serverContent;
    if (!content) return;
    for (const part of content.modelTurn?.parts ?? []) if (part.inlineData?.data) audio[stage]++;
    if (content.outputTranscription?.text) words[stage] += content.outputTranscription.text;
    if (content.turnComplete) {
        console.log(JSON.stringify({ completedStage: stage, audio: audio[stage], transcript: words[stage] }));
        if (!audio[stage]) { console.error(JSON.stringify({ error: "Turn ended without audio", stage, words })); process.exitCode = 1; clearTimeout(timer); socket.close(); return; }
        if (stage === 0) {
            stage = 1;
            if (!process.env.VOICE_CHECK_SKIP_CONTEXT) send("content", { turns: "Quiet context: report selection is still pending. Do not announce this note.", turnComplete: false });
            setTimeout(() => { if (audio[1]) { console.error("Quiet context unexpectedly produced audio"); process.exitCode = 1; clearTimeout(timer); socket.close(); return; } send("content", { turns: "I mean the research report. Please say got it so I know you heard me.", turnComplete: true }); }, 3000);
        } else {
            passed = true; console.log(JSON.stringify({ pass: true, audioChunks: audio, transcript: words })); clearTimeout(timer); socket.close();
        }
    }
});
socket.on("error", () => { console.error("Could not connect to local voice gateway"); clearTimeout(timer); process.exitCode = 1; });
socket.on("close", () => { clearTimeout(timer); if (!passed) { console.error("Voice socket closed before both turns completed"); process.exitCode = 1; } });
