from playwright.sync_api import sync_playwright
import json

def test_naver():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        def handle_response(response):
            if "api/search/allSearch" in response.url:
                print(f"URL: {response.url}")
                try:
                    data = response.json()
                    print(json.dumps(data, ensure_ascii=False)[:1000])
                    # Save to file to inspect
                    with open("naver_test_response.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print("Saved to naver_test_response.json")
                except Exception as e:
                    print("Error:", e)

        page.on("response", handle_response)
        
        # We need to simulate user search.
        # Sometimes direct URL works, sometimes we need to type and press enter.
        url = "https://map.naver.com/p/search/%EC%B9%B4%ED%8E%98?c=15.00,0,0,0,dh"
        print("Navigating to:", url)
        page.goto(url)
        page.wait_for_timeout(10000)
        
        browser.close()

if __name__ == "__main__":
    test_naver()
