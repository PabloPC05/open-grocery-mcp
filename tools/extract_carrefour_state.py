#!/usr/bin/env python3
"""Extract __INITIAL_STATE__ from Carrefour HTML."""

import asyncio
import json
import re
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-ES",
        )
        
        page = await context.new_page()
        
        # Capture XHR requests
        api_calls = []
        
        def handle_request(request):
            url = request.url
            if '/search-api/' in url:
                api_calls.append({
                    "url": url,
                    "method": request.method,
                    "headers": dict(request.headers),
                })
                print(f"[API] {request.method} {url}")
        
        page.on("request", handle_request)
        
        print("Navigating to search page...")
        await page.goto("https://www.carrefour.es/supermercado/c?q=leche", timeout=120000)
        await asyncio.sleep(10)
        
        html = await page.content()
        print(f"HTML length: {len(html)}")
        
        # Extract __INITIAL_STATE__
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});?\s*(?:window\.|</script>)', html, re.DOTALL)
        if match:
            try:
                state_json = match.group(1)
                state = json.loads(state_json)
                
                print("\n__INITIAL_STATE__ found!")
                print(f"Top-level keys: {list(state.keys())}")
                
                # Save full state
                with open("/workspace/local-captures/carrefour_initial_state.json", "w") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                print("Saved to carrefour_initial_state.json")
                
                # Extract search config
                if "config" in state and "search" in state.get("config", {}):
                    search_config = state["config"]["search"]
                    print(f"\nSearch config keys: {list(search_config.keys())}")
                    print(f"Search config: {json.dumps(search_config, indent=2)[:1000]}")
                
            except Exception as e:
                print(f"Error parsing state: {e}")
        else:
            print("No __INITIAL_STATE__ found")
        
        # Save captured API calls
        with open("/workspace/local-captures/carrefour_api_calls.json", "w") as f:
            json.dump(api_calls, f, indent=2)
        print(f"\nCaptured {len(api_calls)} API calls")
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
