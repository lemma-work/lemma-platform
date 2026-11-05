import Link from "next/link";
export default function NotFound() {
    return <main className="screen"><div className="screen__inner"><p>This page could not be found.</p><Link href="/home">Open your teammates</Link></div></main>;
}
