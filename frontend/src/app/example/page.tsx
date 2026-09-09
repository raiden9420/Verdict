import type { Metadata } from "next";
import { ExampleWorkspace } from "@/components/ExampleWorkspace";

export const metadata: Metadata = { title: "A worked example — Verdict", description: "Explore an illustrative research review: three findings, their source evidence, and a revision brief." };

export default function ExamplePage() { return <ExampleWorkspace />; }
