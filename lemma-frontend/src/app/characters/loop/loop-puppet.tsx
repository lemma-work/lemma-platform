"use client";
import { useEffect, useId, useRef } from "react";
import { createLife, lifeGain, SMALL_ABOVE, TEMPO } from "../life";
import styles from "./page.module.css";
export type Mood = "idle" | "listening" | "thinking" | "waiting" | "delighted";
/** How big this is actually drawn, in CSS pixels. It decides two things:
 *  how far the ambient motion has to be exaggerated to stay visible, and
 *  which copy of the art to fetch — a 1254-square render is wasted on a
 *  rail mark, and twenty-one of them is what made the gallery cost 19MB. */
export function LoopPuppet({mood,greeting,paused,pixels=620}:{mood:Mood;greeting:number;paused:boolean;pixels?:number}) {
    const gain=lifeGain(pixels),detail=pixels<=SMALL_ABOVE?"-sm":"";
    const id=useId().replaceAll(":","");
    const host=useRef<HTMLDivElement>(null);
    const root=useRef<SVGGElement>(null), right=useRef<SVGGElement>(null), left=useRef<SVGGElement>(null);
    const wrist=useRef<SVGGElement>(null), leg=useRef<SVGGElement>(null);
    const creaseA=useRef<SVGPathElement>(null), creaseB=useRef<SVGPathElement>(null);
    const upperA=useRef<SVGPathElement>(null), upperB=useRef<SVGPathElement>(null);
    const pupilA=useRef<SVGGElement>(null),pupilB=useRef<SVGGElement>(null);
    const lidA=useRef<SVGPathElement>(null),lidB=useRef<SVGPathElement>(null);
    const eyeA=useRef<SVGGElement>(null),eyeB=useRef<SVGGElement>(null);
    const inputs=useRef({mood,greeting,paused});
    useEffect(()=>{inputs.current={mood,greeting,paused};},[mood,greeting,paused]);
    useEffect(()=>{
        const element=host.current;if(!element)return;
        const motion=matchMedia("(prefers-reduced-motion: reduce)");
        const target={x:0,y:0,inside:false,entered:0};
        const move=(e:PointerEvent)=>{const b=element.getBoundingClientRect();target.x=Math.max(-1,Math.min(1,(e.clientX-b.left-b.width/2)/(b.width/2)));target.y=Math.max(-1,Math.min(1,(e.clientY-b.top-b.height/2)/(b.height/2)));if(!target.inside)target.entered=performance.now();target.inside=true;};
        const leave=()=>{target.inside=false;};
        element.addEventListener("pointermove",move);element.addEventListener("pointerleave",leave);
        let visible=true;const observer=new IntersectionObserver(e=>{visible=e[0].isIntersecting;});observer.observe(element);
        let frame=0,last=performance.now(),time=0,nextBlink=3,blinkAt=-10,helloAt=-10,previousGreeting=0;
        let x=0,y=0,joy=0,tilt=0,ra=64,la=-72;
        const life=createLife("loop"),duration=3.6*TEMPO;
        const damp=(a:number,b:number,rate:number,dt:number)=>a+(b-a)*(1-Math.exp(-rate*dt));
        function animate(now:number){
            frame=requestAnimationFrame(animate);const dt=Math.min((now-last)/1000,.04);last=now;
            if(!visible||document.hidden)return;
            const input=inputs.current,still=input.paused||motion.matches;
            if(!still)time+=dt;
            if(previousGreeting!==input.greeting){previousGreeting=input.greeting;helloAt=time;}
            const age=time-helloAt;
            const hello=!still&&age<duration;
            const ambient=life(time,!still,hello,gain);
            const happy=input.mood==="delighted"||hello;
            joy=damp(joy,happy?1:input.mood==="idle"?.12:0,still?100:6,dt);
            if(!still&&time>nextBlink){blinkAt=time;nextBlink=time+3.5+Math.random()*3.6;}
            const bp=(time-blinkAt)/.19,blink=!still&&bp>0&&bp<1?Math.sin(bp*Math.PI):0;
            const thinking=input.mood==="thinking",listening=input.mood==="listening";
            // A glance is only ever a fallback: a pointer in the frame outranks
            // whatever he had decided to look at.
            const gx=still?0:hello?0:target.inside?target.x*13:thinking?-11:ambient.glanceX*12;
            const gy=still?0:hello?0:target.inside?target.y*12:thinking?-8:ambient.glanceY*9;
            x=damp(x,gx,18,dt);y=damp(y,gy,18,dt);
            pupilA.current?.setAttribute("transform",`translate(${x} ${y})`);pupilB.current?.setAttribute("transform",`translate(${x} ${y})`);
            // Lower lids rise in a smile; the original eye whites stay fixed.
            const lower=66-joy*23;
            const path=`M-70 80 V${lower} Q0 ${lower-14*joy} 70 ${lower} V80Z`;
            lidA.current?.setAttribute("d",path);lidB.current?.setAttribute("d",path);
            // Close lids over the spherical eyes rather than flattening the eyes.
            const top=-70+blink*145+(thinking?12:0);
            const topPath=`M-72 -80H72V${top}Q0 ${top+10} -72 ${top}Z`;
            upperA.current?.setAttribute("d",topPath);upperB.current?.setAttribute("d",topPath);
            creaseA.current?.setAttribute("opacity",String(Math.pow(blink,5)));creaseB.current?.setAttribute("opacity",String(Math.pow(blink,5)));
            eyeA.current?.setAttribute("transform",`translate(651 443) rotate(${10-joy*4})`);
            eyeB.current?.setAttribute("transform",`translate(768 421) rotate(${9+joy*3})`);
            const dwell=target.inside&&now-target.entered>140;
            tilt=damp(tilt,still?0:happy?-2:listening?2.2:dwell?target.x*1.3:0,3.5,dt);
            // Ambient amplitudes are in viewBox units, where 1254 spans the stage.
            // Up is negative here, so the breath and the hop subtract.
            const lift=(still?0:hello?Math.sin(Math.min(age/duration,1)*Math.PI)*-13:0)-ambient.hop*16-ambient.bob*17-ambient.breath*9;
            const wide=1-ambient.squash*.025-ambient.breath*.008,tall=1+ambient.squash*.05+ambient.breath*.018;
            root.current?.setAttribute("transform",`translate(${ambient.slide*14} ${lift}) rotate(${tilt+ambient.sway*2.2+ambient.twist*3} 740 1110) translate(740 1110) scale(${wide} ${tall}) translate(-740 -1110)`);
            // Three wrist-led waves after the shoulder has arrived; ease in and out.
            const phase=Math.max(0,Math.min(1,(age-.5*TEMPO)/(2.2*TEMPO)));
            const wave=hello?Math.sin(phase*Math.PI*6)*14*Math.sin(phase*Math.PI):0;
            wrist.current?.setAttribute("transform",`rotate(${wave+ambient.fidget*13} 950 710)`);
            const kick=(hello?Math.sin(Math.min(age/duration,1)*Math.PI)*9:0)+ambient.step*11;
            leg.current?.setAttribute("transform",`rotate(${kick} 595 865)`);
            ra=damp(ra,happy?-18:input.mood==="waiting"?-12:64,still?100:7,dt);
            la=damp(la,happy?18:-72,still?100:5,dt);
            right.current?.setAttribute("transform",`rotate(${ra+ambient.drift*3} 740 615)`);
            left.current?.setAttribute("transform",`rotate(${la-ambient.drift*2.2} 400 615)`);
            element!.dataset.wave=hello&&phase>0&&phase<1?"waving":"settled";
            element!.dataset.pose=hello?"greeting":input.mood;
            element!.dataset.gaze=target.inside&&!still?"following":"resting";
        }
        frame=requestAnimationFrame(animate);
        return()=>{cancelAnimationFrame(frame);observer.disconnect();element.removeEventListener("pointermove",move);element.removeEventListener("pointerleave",leave);};
    },[gain]);
    return <div ref={host} className={styles.scene} role="img" aria-label={`Loop, ${mood}`}>
        <svg viewBox="0 0 1254 1254" width="100%" height="100%" aria-hidden="true" style={{overflow:"visible"}}>
            <defs>
                <radialGradient id={`${id}-white`} cx="32%" cy="24%" r="76%"><stop stopColor="#fffdf5"/><stop offset=".45" stopColor="#f5eddf"/><stop offset=".8" stopColor="#ded0bb"/><stop offset="1" stopColor="#ad8c70"/></radialGradient>
                <linearGradient id={`${id}-lid`} x2=".3" y2="1"><stop stopColor="#fff9ed"/><stop offset="1" stopColor="#d9c9b1"/></linearGradient>
                <linearGradient id={`${id}-skin`} x2=".6" y2="1"><stop stopColor="#ff865e"/><stop offset="1" stopColor="#ed502c"/></linearGradient>
                <filter id={`${id}-soft`}><feGaussianBlur stdDeviation="2.1"/></filter>
                <clipPath id={`${id}-forearm`}><path d="M0 0H280L1254 974V1254H0Z"/></clipPath>
                <clipPath id={`${id}-hand`}><path d="M200 0H1254V1054Z"/></clipPath>
                <clipPath id={`${id}-leg`}><path d="M280 860H470L581 842L626 885L503 1017L500 1120H280Z"/></clipPath>
                <mask id={`${id}-torso`}><rect width="1254" height="1254" fill="white"/><path d="M280 860H470L581 842L626 885L503 1017L500 1120H280Z" fill="black"/></mask>
                <radialGradient id={`${id}-black`} cx="32%" cy="22%"><stop stopColor="#484441"/><stop offset=".5" stopColor="#161313"/><stop offset="1" stopColor="#080808"/></radialGradient>
                <clipPath id={`${id}-eye`}><ellipse rx="56" ry="64"/></clipPath>
                <filter id={`${id}-shadow`} x="-40%" y="-40%" width="190%" height="190%"><feDropShadow dx="0" dy="7" stdDeviation="6" floodColor="#792613" floodOpacity=".3"/></filter>
            </defs>
            <g ref={root}>
                <g ref={right}><g transform="translate(220 20) scale(.75)">
                    <image href={`/teammates/loop-v2/arm${detail}.webp`} width="1254" height="1254" clipPath={`url(#${id}-forearm)`}/>
                    <g ref={wrist}><image href={`/teammates/loop-v2/arm${detail}.webp`} width="1254" height="1254" clipPath={`url(#${id}-hand)`}/></g>
                </g></g>
                <g ref={left}><image href={`/teammates/loop-v2/arm${detail}.webp`} width="1254" height="1254" transform="translate(920 20) scale(-.75 .75)"/></g>
                <g ref={leg}><image href={`/teammates/loop-v2/body${detail}.webp`} width="1254" height="1254" clipPath={`url(#${id}-leg)`}/></g>
                <image href={`/teammates/loop-v2/body${detail}.webp`} width="1254" height="1254" mask={`url(#${id}-torso)`}/>
                {[{eye:eyeA,pupil:pupilA,lid:lidA,upper:upperA,crease:creaseA,x:651,y:443},{eye:eyeB,pupil:pupilB,lid:lidB,upper:upperB,crease:creaseB,x:768,y:421}].map((part,i)=><g key={i} ref={part.eye} transform={`translate(${part.x} ${part.y}) rotate(10)`}>
                    <ellipse rx="56" ry="64" fill={`url(#${id}-white)`} filter={`url(#${id}-shadow)`}/>
                    <g clipPath={`url(#${id}-eye)`}>
                        <g ref={part.pupil}><ellipse cy="-2" rx="37" ry="43" fill={`url(#${id}-black)`}/><ellipse cx="-9" cy="-15" rx="10" ry="11" fill="#fff" opacity=".68" filter={`url(#${id}-soft)`}/><ellipse cx="9" cy="12" rx="3" ry="3" fill="#a79d8e" opacity=".35"/></g>
                        <path ref={part.lid} d="M-70 80V64Q0 64 70 64V80Z" fill={`url(#${id}-lid)`} stroke="#a99275" strokeOpacity=".18" strokeWidth="1.5"/>
                        <path ref={part.upper} d="M-72 -80H72V-70Q0 -60 -72 -70Z" fill={`url(#${id}-lid)`}/>
                        <path ref={part.crease} d="M-32 5Q0 20 32 5" fill="none" stroke="#79604c" strokeWidth="3" strokeLinecap="round" opacity="0"/>
                    </g>
                </g>)}
            </g>
        </svg>
    </div>;
}
