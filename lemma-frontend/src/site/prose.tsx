import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
export function Prose({ children }: { children: string }) {
    return (
        <div className="site-prose">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeSlug]}
                components={{
                    table: ({ children }) => (
                        <div className="site-table">
                            <table>{children}</table>
                        </div>
                    ),
                }}
            >
                {children}
            </ReactMarkdown>
        </div>
    );
}
