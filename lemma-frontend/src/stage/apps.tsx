import { useState } from "react";
import Image from "next/image";
import type { Tab } from "@/data";
import { AppsIcon, ArrowRightIcon, PlusIcon } from "@/ui/icons";
import { APP_CATEGORIES } from "./app-ideas";

export function AppsPane({ name, tabs, onOpen, onAsk }: {
    name: string;
    tabs: Tab[];
    onOpen: (id: string) => void;
    onAsk: (text: string) => void;
}) {
    const [categoryId, setCategoryId] = useState(APP_CATEGORIES[0].id);
    const category = APP_CATEGORIES.find(entry => entry.id === categoryId) ?? APP_CATEGORIES[0];
    const previewRowSplit = category.previewRowSplit ?? 0.5;
    const apps = tabs.filter(tab => tab.kind === "app");

    return <div className="pane library-pane apps-pane">
        <div className="apps-pane__content">
            <header className="apps-pane__header">
                <div><h1>Apps</h1><p>Open your apps or ask {name} to build something new.</p></div>
                <button className="btn" onClick={() => onAsk("I'd like to build an app. Help me work out what it should do.")}><PlusIcon size={16} />Describe an app</button>
            </header>
            {apps.length > 0 && <div className="apps-pane__existing" aria-label="Your apps">{apps.map(app => <button className="apps-pane__app" key={app.id} onClick={() => onOpen(app.id)}><span className="apps-pane__icon"><AppsIcon size={20} /></span><span>{app.label}</span><ArrowRightIcon size={16} /></button>)}</div>}
            <section className="app-catalog" aria-labelledby="app-catalog-heading">
                <header className="app-catalog__header"><h2 id="app-catalog-heading">App ideas</h2><span>Example previews · Built to fit your needs</span></header>
                <div className="app-catalog__categories" role="group" aria-label="App categories">
                    {APP_CATEGORIES.map(entry => <button key={entry.id} aria-pressed={entry.id === category.id} onClick={() => setCategoryId(entry.id)}>{entry.name}</button>)}
                </div>
                <div className="app-catalog__grid" aria-label={`${category.name} app ideas`}>
                    {category.ideas.map((idea, index) => <article className="app-idea" key={category.id + idea.name}>
                        <div className="app-idea__preview">
                            <Image src={`/app-ideas/${category.id}-collection.png`} alt={`Example ${idea.name.toLowerCase()} interface`} width={1536} height={1024} sizes="(max-width: 600px) 200vw, (max-width: 1100px) 100vw, 650px" style={{ left: index % 2 === 0 ? "0" : "-100%", top: index < 2 ? "0" : `${-100 * previewRowSplit / (1 - previewRowSplit)}%`, height: `${100 / (index < 2 ? previewRowSplit : 1 - previewRowSplit)}%` }} />
                        </div>
                        <div className="app-idea__body"><h3>{idea.name}</h3><p>{idea.description}</p><button onClick={() => onAsk(idea.prompt)} aria-label={`Build ${idea.name} with ${name}`}><span>Build with {name}</span><ArrowRightIcon size={16} /></button></div>
                    </article>)}
                </div>
            </section>
        </div>
    </div>;
}
