"""Create the interactive WPP/AdTech Graphify demo expected by /demo?tenant=marketing."""

from pathlib import Path

import networkx as nx

from graphify.export import to_html, to_json


OUT = Path("data/wpp_demo/graphify-out")


def main() -> None:
    graph = nx.Graph()
    communities = {
        0: ["nova", "wpp", "agency", "marketing", "eu-desk", "regional-director"],
        1: ["sow", "brand-guideline", "brand-council", "media-plan", "campaign-brief"],
        2: ["dpp", "privacy-officer", "gdpr", "eu-markets", "article-9"],
        3: ["summer-rush", "eu-q3", "germany", "poland", "sports-betting"],
        4: ["google-ads", "meta", "tiktok", "youtube", "social-content"],
        5: ["music-streaming", "companion-apps", "content-safety", "placements"],
    }
    labels = {
        0: "Account & Agency", 1: "Authority Documents", 2: "Privacy & Regulation",
        3: "Campaign & Markets", 4: "Media Channels", 5: "Content Placement",
    }

    nodes = {
        "nova": "NOVA BEVERAGES GLOBAL", "wpp": "WPP Open — Team Meridian, EU Desk",
        "agency": "WPP Open", "marketing": "Regional marketing teams", "eu-desk": "EU Desk",
        "regional-director": "EU Desk Regional Director", "sow": "Statement of Work",
        "brand-guideline": "Brand Guideline", "brand-council": "Nova Beverages Global Brand Council",
        "media-plan": "Media Plan", "campaign-brief": "Campaign Brief",
        "dpp": "Data Privacy Policy", "privacy-officer": "Nova Beverages Global Data Protection Officer",
        "gdpr": "GDPR", "eu-markets": "EU markets", "article-9": "Article 9",
        "summer-rush": "Nova Summer Rush", "eu-q3": "EU Q3", "germany": "DE", "poland": "PL",
        "sports-betting": "Gambling and sports-betting placements", "google-ads": "Google Ads",
        "meta": "Meta", "tiktok": "TikTok", "youtube": "YouTube", "social-content": "Alcohol-adjacent content",
        "music-streaming": "Music streaming and sports-betting companion apps",
        "companion-apps": "Sports streaming and sports-betting companion apps",
        "content-safety": "Brand safety and advertisement restrictions", "placements": "EU retail season",
    }
    node_to_community = {node: cid for cid, members in communities.items() for node in members}
    for node_id, label in nodes.items():
        graph.add_node(
            node_id, label=label, community=node_to_community[node_id], file_type="concept",
            source_file="WPP AdTech presentation scenario", source_location="marketing tenant",
        )

    relations = [
        ("nova", "wpp", "engages"), ("nova", "summer-rush", "runs"), ("nova", "sow", "governed_by"),
        ("nova", "dpp", "governed_by"), ("nova", "brand-guideline", "governed_by"),
        ("wpp", "agency", "operates_as"), ("wpp", "eu-desk", "staffed_by"), ("eu-desk", "marketing", "coordinates"),
        ("eu-desk", "regional-director", "reports_to"), ("regional-director", "campaign-brief", "approves"),
        ("sow", "brand-guideline", "prevails_over"), ("sow", "media-plan", "prevails_over"),
        ("sow", "campaign-brief", "contradicts"), ("brand-guideline", "brand-council", "issued_by"),
        ("brand-guideline", "campaign-brief", "constrains"), ("dpp", "privacy-officer", "issued_by"),
        ("dpp", "gdpr", "implements"), ("gdpr", "eu-markets", "applies_to"), ("gdpr", "article-9", "includes"),
        ("summer-rush", "eu-q3", "scheduled_for"), ("summer-rush", "germany", "targets"),
        ("summer-rush", "poland", "targets"), ("summer-rush", "sports-betting", "proposes"),
        ("summer-rush", "google-ads", "uses"), ("summer-rush", "meta", "uses"), ("summer-rush", "tiktok", "uses"),
        ("summer-rush", "youtube", "uses"), ("summer-rush", "social-content", "adjacent_to"),
        ("sports-betting", "companion-apps", "includes"), ("companion-apps", "music-streaming", "includes"),
        ("content-safety", "placements", "restricts"), ("content-safety", "companion-apps", "restricts"),
        ("sow", "sports-betting", "prohibits"), ("dpp", "sports-betting", "prohibits_inference_for"),
    ]
    for source, target, relation in relations:
        graph.add_edge(source, target, relation=relation, confidence="EXTRACTED", weight=1.0)

    OUT.mkdir(parents=True, exist_ok=True)
    to_json(graph, communities, str(OUT / "graph.json"), force=True, community_labels=labels)
    to_html(graph, communities, str(OUT / "graph.html"), community_labels=labels)
    print(OUT / "graph.html")


if __name__ == "__main__":
    main()
