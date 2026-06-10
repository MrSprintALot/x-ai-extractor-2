"""
Dedicated X-Post & Referenced Article Context Extractor.
Optimized for 2-4 daily runs. Directly fetches tweet content and automatically
extracts raw text from nested external article links for immediate LLM ingestion.
"""

import re
from typing import Any, Dict, Optional, Tuple
import requests
from bs4 import BeautifulSoup
import streamlit as st

# --- DESIGN LIMITS & CONFIG ---
TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def parse_x_url(url: str) -> Optional[Tuple[str, str]]:
    """Extracts username and status ID from X/Twitter variations."""
    pattern = r"https?://(?:www\.)?(?:mobile\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)"
    match = re.search(pattern, url, re.IGNORECASE)
    return (match.group(1), match.group(2)) if match else None


def fetch_external_article(url: str) -> str:
    """
    Follows a nested link, bypasses t.co redirects, fetches the target HTML,
    and isolates the structural body text while removing script/nav noise.
    """
    try:
        # Resolve potential shorteners/redirects and fetch HTML
        res = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if res.status_code != 200:
            return f"Error: Unable to fetch external content (HTTP {res.status_code})"
        
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Strip structural noise that bloats context tokens
        for element in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
            element.decompose()
            
        # Target main content bodies if semantic tags exist, fallback to body
        main_content = soup.find("main") or soup.find("article") or soup.find("body")
        if not main_content:
            return "Error: Could not isolate content body elements."
            
        # Extract paragraph structures to maintain readable layout
        paragraphs = main_content.find_all(["p", "h1", "h2", "h3"])
        text_blocks = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
        
        return "\n\n".join(text_blocks)[:12000]  # Cap length to prevent context explosion
    except Exception as e:
        return f"Extraction failed: {str(e)}"


def run_extraction_pipeline(url: str) -> str:
    """Executes the combined proxy retrieval and deep article extraction."""
    parsed = parse_x_url(url)
    if not parsed:
        return f"### ❌ Invalid URL Format\nSkipped: `{url}`"
        
    _, status_id = parsed
    api_target = f"https://api.fxtwitter.com/i/status/{status_id}"
    
    try:
        res = requests.get(api_target, headers=HEADERS, timeout=TIMEOUT)
        res.raise_for_status()
        tweet_data = res.json().get("tweet", {})
    except Exception as e:
        return f"### ❌ Connection Error\nFailed to pull data for status `{status_id}`: {str(e)}"

    author = tweet_data.get("author", {}).get("screen_name", "unknown")
    display_name = tweet_data.get("author", {}).get("name", "Unknown")
    text = tweet_data.get("text", "")
    date = tweet_data.get("created_at", "N/A")

    # Build the foundational Markdown payload
    md = [
        "---",
        f"## SOURCE CONTEXT: X Post by @{author}",
        f"- **Author Name:** {display_name}",
        f"- **Timestamp:** {date}",
        f"- **Source Link:** {url}",
        "\n### 𝕏 Post Content:",
        f"> {text}\n"
    ]

    # Automatically hunt for external links in the text body
    urls_in_text = re.findall(r'(https?://[^\s]+)', text)
    # Filter out direct native links to twitter/x media assets if they show up
    article_urls = [u for u in urls_in_text if not any(x in u for x in ["t.co", "x.com", "twitter.com"])]
    
    # If t.co is the only thing provided, we must resolve it to find the real article link
    t_co_urls = [u for u in urls_in_text if "t.co" in u]
    if not article_urls and t_co_urls:
        for t_url in t_co_urls:
            try:
                r = requests.head(t_url, headers=HEADERS, timeout=5, allow_redirects=True)
                if not any(x in r.url for x in ["x.com", "twitter.com"]):
                    article_urls.append(r.url)
            except Exception:
                continue

    # Execute deep extraction on found external web links
    if article_urls:
        md.append("### 📄 Linked External Article Content:")
        for target_link in set(article_urls):
            md.append(f"#### Target Link: {target_link}")
            md.append("```text")
            article_body = fetch_external_article(target_link)
            md.append(article_body)
            md.append("
```\n")
            
    md.append("---\n")
    return "\n".join(md)


# --- APPLICATION INTERFACE ---
def main() -> None:
    st.set_page_config(page_title="AI Context Prep", page_icon="⚙️", layout="centered")
    st.title("𝕏 + Article Context Packer")
    st.markdown("Transforms targeted X links and their referenced articles into optimized payloads for AI conversation spaces.")

    links_input = st.text_area(
        "Paste target URLs (supports up to 4 line-separated entries):",
        placeholder="https://x.com/user/status/... ",
        height=120
    )

    if st.button("Generate Context Payload", type="primary"):
        cleaned_inputs = [line.strip() for line in links_input.split("\n") if line.strip()]
        
        if not cleaned_inputs:
            st.warning("Input buffer empty.")
            return
            
        if len(cleaned_inputs) > 5:
            st.error("Input exceeds normal low-volume constraints. Limit processing block to max 4-5 links.")
            return

        compiled_payloads = []
        with st.spinner("Extracting content blocks and parsing nested layers..."):
            for link in cleaned_inputs:
                compiled_payloads.append(run_extraction_pipeline(link))

        final_output = "\n".join(compiled_payloads)
        st.success("Compilation Complete.")
        
        # Large view area for direct copying
        st.text_area("LLM-Ready Markdown Block:", value=final_output, height=450)


if __name__ == "__main__":
    main()
