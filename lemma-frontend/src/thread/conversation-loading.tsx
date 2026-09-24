/** Use the transcript's own geometry so loading inherits message layout changes. */
export function ConversationLoading() {
    return (
        <div className="conversation-loading convo" role="status" aria-label="Loading conversation">
            <div aria-hidden="true">
                <div className="day"><span className="loading-shape conversation-loading__name" /></div>
                <div className="msg msg--you">
                    <div className="msg__head"><span className="loading-shape conversation-loading__name" /></div>
                    <div className="msg__body conversation-loading__question"><span className="loading-shape" /><span className="loading-shape" /></div>
                </div>
                <div className="msg msg--reply">
                    <span className="loading-shape conversation-loading__avatar" />
                    <div className="msg__col">
                        <div className="msg__head"><span className="loading-shape conversation-loading__name" /></div>
                        <div className="msg__body">
                            <div className="said conversation-loading__answer"><span className="loading-shape" /><span className="loading-shape" /><span className="loading-shape" /></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
