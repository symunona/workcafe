from playwright.sync_api import sync_playwright
import json

def test_naver():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def handle_response(response):
            if "api/search/allSearch" in response.url:
                print(f"URL: {response.url}")
                print(f"Status: {response.status}")
                try:
                    data = response.json()
                    print("JSON parsed successfully")
                    with open("naver_test_response.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print("Error parsing JSON:", e)

        page.on("response", handle_response)

        url = "https://map.naver.com/p/search/%EC%B9%B4%ED%8E%98?c=15.00,0,0,0,dh"
        print("Navigating to:", url)
        page.goto(url)
        page.wait_for_timeout(5000)

        browser.close()

if __name__ == "__main__":
    test_naver()
