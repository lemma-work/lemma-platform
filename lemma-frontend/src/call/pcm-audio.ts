"use client";

// Raw PCM16 mic capture and playback. Uses ScriptProcessorNode rather than an
// AudioWorklet — deprecated, but it needs no separate worklet file to serve,
// which keeps this prototype to two files instead of three.
//
// Playback is 24kHz for both models we speak to. Capture is not: Gemini Live
// wants 16kHz and GPT-Live wants 24, so the rate is the caller's to name and
// the transport it is talking to is what knows the answer.

const DEFAULT_MIC_SAMPLE_RATE = 16000;
const PLAYBACK_SAMPLE_RATE = 24000;
const CAPTURE_BUFFER_SAMPLES = 2048;
const ANALYSER_FFT_SIZE = 256;

function floatTo16BitPCM(input: Float32Array): Int16Array {
    const output = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
        const clamped = Math.max(-1, Math.min(1, input[i]));
        output[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    }
    return output;
}

function base64FromInt16(pcm: Int16Array): string {
    const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
}

function int16FromBase64(base64: string): Int16Array {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Int16Array(bytes.buffer);
}

// Shared by mic and playback: a 0..1 RMS reader off a live AnalyserNode. Read
// on demand (from a caller's rAF loop) rather than pushed, so the audio graph
// never pays for a level nobody's currently animating.
function attachLevelMeter(context: AudioContext): { analyser: AnalyserNode; getLevel: () => number } {
    const analyser = context.createAnalyser();
    analyser.fftSize = ANALYSER_FFT_SIZE;
    analyser.smoothingTimeConstant = 0.6;
    const buffer = new Uint8Array(analyser.frequencyBinCount);

    return {
        analyser,
        getLevel() {
            analyser.getByteTimeDomainData(buffer);
            let sumSquares = 0;
            for (let i = 0; i < buffer.length; i++) {
                const centered = (buffer[i] - 128) / 128;
                sumSquares += centered * centered;
            }
            return Math.min(1, Math.sqrt(sumSquares / buffer.length) * 4);
        },
    };
}

export interface MicStream {
    /** Current mic input level, 0..1. Cheap — call from a rAF loop. */
    getLevel(): number;
    stop(): void;
}

export async function startMicPCMStream(
    onChunk: (base64Pcm: string) => void,
    sampleRate: number = DEFAULT_MIC_SAMPLE_RATE,
): Promise<MicStream> {
    const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate, echoCancellation: true, noiseSuppression: true },
    });
    const audioContext = new AudioContext({ sampleRate });
    const source = audioContext.createMediaStreamSource(mediaStream);
    const processor = audioContext.createScriptProcessor(CAPTURE_BUFFER_SAMPLES, 1, 1);
    const { analyser, getLevel } = attachLevelMeter(audioContext);

    // A ScriptProcessorNode only fires onaudioprocess while it's part of a live
    // graph reaching the destination — route it through a muted gain node so the
    // mic is captured without being echoed back out of the speakers. The
    // analyser taps the same signal in parallel, upstream of the mute.
    const mute = audioContext.createGain();
    mute.gain.value = 0;

    processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        onChunk(base64FromInt16(floatTo16BitPCM(input)));
    };

    source.connect(analyser);
    source.connect(processor);
    processor.connect(mute);
    mute.connect(audioContext.destination);

    return {
        getLevel,
        stop() {
            processor.disconnect();
            source.disconnect();
            analyser.disconnect();
            mute.disconnect();
            mediaStream.getTracks().forEach((track) => track.stop());
            void audioContext.close();
        },
    };
}

export interface PCMPlayer {
    /** Queue a base64 PCM16 chunk (24kHz mono) for gapless playback. */
    push(base64Pcm: string): void;
    /** Stop current and scheduled audio immediately on interruption. */
    clear(): void;
    /** Current playback output level, 0..1. Cheap — call from a rAF loop. */
    getLevel(): number;
    isPlaying(): boolean;
    stop(): void;
}

export function createPCMPlayer(): PCMPlayer {
    const audioContext = new AudioContext({ sampleRate: PLAYBACK_SAMPLE_RATE });
    /* A context built outside a user gesture starts suspended, and a suspended
       context accepts every scheduled buffer in silence — audio arriving and
       nothing playing, with no error anywhere to say so. The call starts on a
       click, but `start()` awaits a dynamic import first, and the gesture does
       not survive the await. Resuming is a no-op when it was never suspended. */
    const wake = () => { if (audioContext.state === "suspended") void audioContext.resume(); };
    wake();
    const { analyser, getLevel } = attachLevelMeter(audioContext);
    analyser.connect(audioContext.destination);
    let nextStartTime = audioContext.currentTime;
    let stopped = false;
    const sources = new Set<AudioBufferSourceNode>();
    const clear = () => {
        for (const source of sources) {
            source.onended = null;
            try { source.stop(); } catch { /* Already ended. */ }
            source.disconnect();
        }
        sources.clear();
        nextStartTime = audioContext.currentTime;
    };

    return {
        push(base64Pcm: string) {
            if (stopped) return;
            /* Also on the way in: autoplay policy can suspend a context that
               was running, and the first chunk is when it matters. */
            wake();
            const pcm = int16FromBase64(base64Pcm);
            const float32 = new Float32Array(pcm.length);
            for (let i = 0; i < pcm.length; i++) float32[i] = pcm[i] / 0x8000;

            const buffer = audioContext.createBuffer(1, float32.length, PLAYBACK_SAMPLE_RATE);
            buffer.copyToChannel(float32, 0);

            const source = audioContext.createBufferSource();
            source.buffer = buffer;
            source.connect(analyser);
            sources.add(source);
            source.onended = () => { sources.delete(source); source.disconnect(); };

            const startTime = Math.max(nextStartTime, audioContext.currentTime);
            source.start(startTime);
            nextStartTime = startTime + buffer.duration;
        },
        clear,
        getLevel,
        isPlaying: () => sources.size > 0,
        stop() {
            if (stopped) return;
            stopped = true;
            clear();
            analyser.disconnect();
            void audioContext.close();
        },
    };
}
