import { widgetThemeStyle } from "./widget-theme";

export function framedDocument(html: string | undefined, id: string): string | undefined {
    if (html === undefined) return undefined;
    const bridge = `<script>(()=>{let last=0;const send=()=>{const h=Math.ceil(Math.max(document.body?.scrollHeight||0,document.documentElement.scrollHeight));if(h!==last){last=h;parent.postMessage({type:'lemma:preview-height',id:${JSON.stringify(id).replace(/</g, "\\u003c")},height:h},'*')}};new ResizeObserver(send).observe(document.documentElement);addEventListener('load',send);send();addEventListener('message',e=>{if(e.source!==parent)return;const d=e.data;if(!d||d.type!=='lemma-widget-theme'||!d.tokens)return;const r=document.documentElement;for(const k in d.tokens){if(k.indexOf('--lemma-widget-')===0)r.style.setProperty(k,d.tokens[k])}r.dataset.lemmaTheme=d.theme;r.style.colorScheme=d.theme});window.lemma={compose:(text,options)=>{parent.postMessage({type:'lemma-compose',id:String(Date.now())+Math.random(),text:String(text),newConversation:!!(options&&options.newConversation)},'*')}}})()<\/script>`;
    return `<style>${widgetThemeStyle()}</style>` + html + bridge;
}
