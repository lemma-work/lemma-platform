import type { JsonLdSchema } from '@/lib/seo/structured-data';

/**
 * `</script>` inside a JSON string literal still closes the surrounding script
 * element — the HTML tokenizer never learns that it is inside JSON. Escaping
 * every `<` to its unicode form keeps the payload byte-identical to a parser
 * and inert to the tokenizer.
 */
function serialize(schema: JsonLdSchema | JsonLdSchema[]): string {
    return JSON.stringify(schema).replace(/</g, '\\u003c');
}

/**
 * Emits structured data for the page that renders it.
 *
 * A server component on purpose: the whole value of JSON-LD is that it is in
 * the HTML a crawler receives, so this must never be deferred to a client
 * render. Passing an array emits one script per schema, which is what Google
 * expects when a page is both an article and a breadcrumb trail.
 */
export function JsonLd({ schema }: { schema: JsonLdSchema | JsonLdSchema[] }) {
    const documents = Array.isArray(schema) ? schema : [schema];
    return (
        <>
            {documents.map((document, index) => (
                <script
                    key={index}
                    type="application/ld+json"
                    dangerouslySetInnerHTML={{ __html: serialize(document) }}
                />
            ))}
        </>
    );
}
