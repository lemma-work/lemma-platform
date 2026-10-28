"use client";

// Raw PCM16 mic capture and playback. Uses ScriptProcessorNode rather than an
// AudioWorklet — deprecated, but it needs no separate worklet file to serve.
//
// Capture is 16kHz, the rate the call server takes from every caller; playback
// is the rate the server names when the call is ready (24kHz for every engine
// it has). Both are raw bytes: the call's socket carries binary frames.

const DEFAULT_MIC_SAMPLE_RATE = 16000;
const DEFAULT_PLAYBACK_SAMPLE_RATE = 24000;
// 64 ms at 16 kHz: small enough that a turn's end is not held back a frame.
const CAPTURE_BUFFER_SAMPLES = 1024;
const ANALYSER_FFT_SIZE = 256;

function floatTo16BitPCM(input: Float32Array): Int16Array {
    const output = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
        const clamped = Math.max(-1, Math.min(1, input[i]));
        output[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    }
    return output;
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
    onChunk: (pcm: Int16Array) => void,
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
        onChunk(floatTo16BitPCM(input));
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
    /** Queue a PCM16 mono chunk for gapless playback. */
    push(pcm: ArrayBuffer): void;
    /** Stop current and scheduled audio immediately on interruption. */
    clear(): void;
    /** Current playback output level, 0..1. Cheap — call from a rAF loop. */
    getLevel(): number;
    isPlaying(): boolean;
    stop(): void;
}

export function createPCMPlayer(sampleRate: number = DEFAULT_PLAYBACK_SAMPLE_RATE): PCMPlayer {
    const audioContext = new AudioContext({ sampleRate });
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
        push(bytes: ArrayBuffer) {
            if (stopped) return;
            /* Also on the way in: autoplay policy can suspend a context that
               was running, and the first chunk is when it matters. */
            wake();
            const pcm = new Int16Array(bytes, 0, Math.floor(bytes.byteLength / 2));
            const float32 = new Float32Array(pcm.length);
            for (let i = 0; i < pcm.length; i++) float32[i] = pcm[i] / 0x8000;

            if (!float32.length) return;
            const buffer = audioContext.createBuffer(1, float32.length, sampleRate);
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
