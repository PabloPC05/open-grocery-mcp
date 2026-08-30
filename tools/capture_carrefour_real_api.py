#!/usr/bin/env python3
"""Capture real Carrefour Spain search API structure."""

import asyncio
import json
from playwright.async_api import async_playwright, Route


async def main():
    captured_api_calls = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-ES",
        )
        
        page = await context.new_page()
        
        async def handle_route(route: Route):
            request = route.request
            url = request.url
            
            if '/search-api/query/v1/search' in url:
                print(f"\n{'='*60}")
                print(f"[FOUND] {request.method} {url}")
                print(f"{'='*60}")
                
                response = await route.fetch()
                body = await response.body()
                
                try:
                    text = body.decode('utf-8')
                    data = json.loads(text)
                    
                    captured_api_calls.append({
                        "url": url,
                        "method": request.method,
                        "status": response.status,
                        "request_headers": dict(request.headers),
                        "response_headers": dict(response.headers),
                        "response_body": data,
                    })
                    
                    print(f"Status: {response.status}")
                    print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                    print(f"Body sample: {json.dumps(data, indent=2, ensure_ascii=False)[:1500]}")
                    
                except Exception as e:
                    print(f"Error parsing: {e}")
                    print(f"Body: {body[:500]}")
                
                await route.fulfill(response=response)
            else:
                await route.continue_()
        
        await page.route("**/*", handle_route)
        
        print("Navigating to Carrefour supermercado...")
        await page.goto("https://www.carrefour.es/supermercado", timeout=120000)
        print("Page loaded, waiting...")
        await asyncio.sleep(5)
        
        print("\nLooking for search box...")
        for selector in [
            'input[type="search"]',
            'input[placeholder*="Busca"]',
            'input[name="q"]',
            '#search-input',
            '[data-test*="search"]',
        ]:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    print(f"Found search: {selector}")
                    await element.click()
                    await asyncio.sleep(1)
                    await element.fill("leche")
                    await asyncio.sleep(2)
                    await element.press("Enter")
                    print("Search submitted, waiting for API call...")
                    await asyncio.sleep(10)
                    break
            except Exception:
                continue
        
        await browser.close()
    
    output_file = "/workspace/local-captures/carrefour_real_api_capture.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "captured_api_calls": captured_api_calls,
            "total_captures": len(captured_api_calls),
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*60}")
    print(f"Captured {len(captured_api_calls)} API calls")
    print(f"Results saved to {output_file}")
    
    if captured_api_calls:
        for call in captured_api_calls:
            print(f"\n{call['method']} {call['url']}")
            print(f"Status: {call['status']}")


if __name__ == "__main__":
    asyncio.run(main())
