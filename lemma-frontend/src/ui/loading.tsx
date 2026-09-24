/** A quiet shared progress mark; the accessible label describes the operation. */
export function LoadingIndicator({ label = "Loading", inline = false }: { label?: string; inline?: boolean }) {
    return <span className={`loading-indicator${inline ? " loading-indicator--inline" : ""}`} role="status" aria-label={label}>
        <span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" />
    </span>;
}

export function LoadingRows({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
    return <div className="loading-rows" role="status" aria-label={label}>
        {Array.from({ length: rows }, (_, index) => <div className="loading-rows__row" key={index} aria-hidden="true"><span className="loading-shape" /><span className="loading-shape" /></div>)}
    </div>;
}
