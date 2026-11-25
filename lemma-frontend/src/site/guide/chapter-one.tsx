import { Chapter, Figure, Section, Statement } from "./parts";

/** A numbered marker that ties a line of a figure to its note. */
function Mark({ n }: { n: number }) {
    return <i className="gb-mark" aria-label={`note ${n}`}>{n}</i>;
}

/** Chapter one: how most people use AI, and what that leaves out. Figures 1 to 5. */
export function ChapterOne() {
    return (
        <>
            <Chapter n={1} />
            <Section id="week">
                <p>Some of this will be familiar.</p>
                <ul className="gb-dots">
                    <li>Every Monday you paste the same background into a new chat.</li>
                    <li>One colleague gets remarkable results from AI. Nobody else knows how, and it has not spread.</li>
                    <li>The perfect reply to a difficult customer is in someone&rsquo;s chat history, where the next person will never find it.</li>
                    <li>You would let the AI send the follow-ups if you could see what it sent.</li>
                    <li>You correct the same mistake every week, because the correction never sticks.</li>
                </ul>
            </Section>
            <Statement label="the cause" line="None of this is the model’s fault.">
                <p>A smarter model would do every one of these things just as reliably. The trouble is where the AI sits: beside one person, inside one conversation, with nothing left behind when the conversation ends.</p>
            </Statement>
            <Section id="shared">
                <p>Take a quote for a customer. The seller writes it. Finance set the prices it uses. A manager has to approve the discount. The customer reads it and writes back. Four people, one document, and most work looks like this once you count.</p>
                <Figure n={1} title="One quote, four people">
                    <div className="gb-annot">
                        <div className="gb-card gb-doc">
                            <div className="gb-doc-h">
                                <span>Quote</span>
                                <span className="gb-tag">draft</span>
                            </div>
                            <dl className="gb-doc-who">
                                <div><dt>From</dt><dd>Sales <Mark n={1} /></dd></div>
                                <div><dt>To</dt><dd>A school, existing customer <Mark n={4} /></dd></div>
                            </dl>
                            <div className="gb-doc-lines">
                                <span>40 × school chair</span><span className="gb-num">@ 120.00 <Mark n={2} /></span><span className="gb-num">4,800.00</span>
                                <span>Discount 18% <Mark n={3} /></span><span /><span className="gb-num">−864.00</span>
                                <b>Total</b><span /><b className="gb-num">3,936.00</b>
                            </div>
                        </div>
                        <ol className="gb-notes">
                            <li><span><b>The seller</b> writes it. Their AI sees what they paste in, and nothing else.</span></li>
                            <li><span><b>Finance</b> set the prices, and changed this one on Tuesday.</span></li>
                            <li><span><b>A manager</b> has to approve any discount over 15%.</span></li>
                            <li><span><b>The customer</b> reads it, and writes back.</span></li>
                        </ol>
                    </div>
                </Figure>
                <p>The AI most of us use helps one of the four. It does not know the price changed on Tuesday, or what this customer was promised in March, because those facts live with the other three.</p>
                <p>For AI to do this work, and not only help with a slice of it, the work has to live where everyone can reach it: the customers, the prices, the quotes and their history, in one copy that people and AI both use.</p>
                <p>Shared does not mean open. The seller should not be able to edit the price list, and neither should an AI working for the seller. So the shared place needs rules about who can see and change what, and the AI lives under the same rules as the person it works for.</p>
            </Section>
            <Section id="chat">
                <p>Chat is the best tool we have for thinking. You can ask something loose, change your mind halfway and follow a thread you did not plan. For work you have never done before, nothing beats it.</p>
                <p>Work you do every week asks for things a conversation cannot give. It has to remember what was decided last time. It has to start on its own when Monday comes. A colleague has to be able to see where it stands without reading your thread. And a draft that needs approval needs a place with a button beside it, not a spot forty messages up.</p>
                <Figure n={2} title="The same draft, in a chat and on a screen">
                    <div className="gb-pair">
                        <div className="gb-pane">
                            <p className="gb-pane-k">in a chat</p>
                            <div className="gb-card gb-chat" aria-hidden="true">
                                <p className="gb-msg">Here&rsquo;s the quote: 40 school chairs at 120.00, 10% education discount…</p>
                                <p className="gb-msg gb-me">make it 18%, they&rsquo;re comparing us with someone</p>
                                <p className="gb-msg">Updated. Total is now 3,936.00. Want me to add delivery terms?</p>
                                <p className="gb-msg gb-me">no. who has to sign off over 15% again?</p>
                                <p className="gb-msg">Usually a manager. You could send it to them with a note…</p>
                            </div>
                            <p className="gb-cap">The draft is somewhere up there, in one person&rsquo;s thread.</p>
                        </div>
                        <div className="gb-pane">
                            <p className="gb-pane-k">on a screen made for it</p>
                            <div className="gb-card gb-app" aria-hidden="true">
                                <div className="gb-app-h"><span>Quotes waiting for a yes</span><span className="gb-count">3</span></div>
                                <div className="gb-app-r"><span>School chairs</span><span className="gb-num">3,936.00</span><span className="gb-flag">18%</span><span className="gb-btn">Approve</span></div>
                                <div className="gb-app-r"><span>Lab stools</span><span className="gb-num">1,210.00</span><span className="gb-pct">10%</span><span className="gb-btn">Approve</span></div>
                                <div className="gb-app-r"><span>Teacher desks</span><span className="gb-num">8,400.00</span><span className="gb-pct">12%</span><span className="gb-btn">Approve</span></div>
                            </div>
                            <p className="gb-cap">Every draft in one list, with the button beside it, for whoever says yes.</p>
                        </div>
                    </div>
                </Figure>
                <p className="gb-pull gb-wide">Chat is where work starts. It is not where work should live.</p>
                <p>Once a job repeats, it deserves a screen of its own. The AI does the work behind the screen. People use the screen to see the work, check it and change it.</p>
            </Section>
            <Section id="steering">
                <p>How good AI&rsquo;s work is depends mostly on how well it is steered, and there are three moments to steer it.</p>
                <ul className="gb-dots">
                    <li><span><b className="gb-lead">Before.</b> You say how the work is done. Keep quotes to one page. Never promise a delivery date.</span></li>
                    <li><span><b className="gb-lead">During.</b> It stops and asks. This discount is over the limit. Send it anyway?</span></li>
                    <li><span><b className="gb-lead">After.</b> You correct the result. You cut the opening line and fix a price.</span></li>
                </ul>
                <p>In a chat, all three happen by typing, and all three are lost. The instruction lasts as long as the conversation. The question goes to whoever is in it. The correction fixes one draft and teaches nothing.</p>
                <p>And the person typing is often not the person who knows. The seller is in the chat, but the discount rule belongs to finance, and the delivery promise belongs to whoever has to keep it.</p>
                <Figure n={3} title="One correction, two outcomes">
                    <div className="gb-weeks">
                        <span />
                        <p className="gb-pane-k">in separate chats</p>
                        <p className="gb-pane-k">in shared work</p>
                        <p className="gb-wk">this week</p>
                        <div className="gb-card gb-snip">
                            <p><span className="gb-del">Delivery within two weeks.</span></p>
                            <p className="gb-who">A manager deletes the line from one draft.</p>
                        </div>
                        <div className="gb-card gb-snip">
                            <p><span className="gb-del">Delivery within two weeks.</span></p>
                            <p className="gb-who">A manager deletes the line, and adds to the instructions: <span className="gb-add">Never promise a delivery date.</span></p>
                        </div>
                        <p className="gb-wk">next week</p>
                        <div className="gb-card gb-snip">
                            <p>&ldquo;Delivery within two weeks.&rdquo;</p>
                            <p className="gb-bad">Another seller&rsquo;s draft makes the same promise.</p>
                        </div>
                        <div className="gb-card gb-snip">
                            <p>&ldquo;We confirm a delivery date when you order.&rdquo;</p>
                            <p className="gb-good">Every draft, from everyone, follows it.</p>
                        </div>
                    </div>
                </Figure>
                <p>When the work is shared, steering lands in the work. The instruction is written once and holds for every run. The question goes to the person who should answer it, and the work waits for them. The correction is made by whoever knows best, and every later draft follows it.</p>
                <p>In a Lemma, anything that matters comes back to a person before it happens, an agent can stop partway through to ask, and every action carries the name of who or what took it. On a good factory floor anyone can stop the line, and nobody is blamed for doing it. An AI that never stops to ask has not been trusted with anything that matters.</p>
            </Section>
            <Section id="learning">
                <p>A personal assistant learns one person&rsquo;s habits. A team&rsquo;s know-how is spread wider than that. The seller knows how this customer likes to be addressed. Finance knows which discounts turned out to be a mistake. Support knows what was promised.</p>
                <p>An AI that sits in the shared work sees every request, every correction and every yes and no, from everyone. What it picks up from one person is there for the next.</p>
                <p>Where that learning is kept matters as much as whether it happens. Hidden inside a model, nobody can check it, and one person&rsquo;s bad habit quietly becomes the team&rsquo;s rule. So a Lemma keeps what it is taught in plain words, beside the work, where anyone on the team can read it, fix it or delete it. What it learns about you alone stays private to you.</p>
                <Figure n={4} title="What it has been taught, kept where you can read it">
                    <div className="gb-card gb-page">
                        <div className="gb-page-h"><span className="gb-tag">taught</span><span>Writing quotes</span></div>
                        <ul>
                            <li><span>Never promise a delivery date.</span><em>from a manager&rsquo;s correction</em></li>
                            <li><span>Schools get the education rate, 10%.</span><em>from finance</em></li>
                            <li><span>Open with the customer&rsquo;s own words.</span><em>from a seller</em></li>
                            <li><span>Anything over 15% off waits for a manager.</span><em>from finance</em></li>
                        </ul>
                        <p className="gb-page-f">Plain words. Anyone on the team can read, fix or delete a line.</p>
                    </div>
                </Figure>
            </Section>
            <Section id="map">
                <p>Two questions place any way of using AI. Who is it for: one person, or everyone who does the work? And what keeps it going: someone asking each time, or the work itself?</p>
                <Figure n={5} title="Two questions, four places to stand">
                    <div className="gb-map">
                        <p className="gb-ax gb-ax-y"><span>it runs</span><span>you ask</span></p>
                        <div className="gb-quad">
                            <div className="gb-q"><h4>a personal agent</h4><p>Runs on its own, for one person. Nobody else can use it or fix it.</p></div>
                            <div className="gb-q gb-on"><h4>a factory</h4><p>A team&rsquo;s work, running in one place that everyone can see and steer.</p></div>
                            <div className="gb-q gb-here"><h4>a chat</h4><p>Good for thinking. Nothing happens unless you ask.</p><span className="gb-pin">most of us</span></div>
                            <div className="gb-q"><h4>shared prompts</h4><p>A document of what worked. Everyone still starts from scratch.</p></div>
                        </div>
                        <p className="gb-ax gb-ax-x"><span>for one person</span><span>for everyone</span></p>
                    </div>
                </Figure>
                <p>Most of us are in the bottom left. A better model does not move anyone on this map, and neither does a cleverer prompt. Something else does, and one group of people found it first.</p>
            </Section>
        </>
    );
}
