from playwright.sync_api import sync_playwright
import urllib.parse
import json

def test_m_kakao_json():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
            viewport={"width": 360, "height": 800}
        )
        page = context.new_page()
        
        api_data = []
        def handle_response(response):
            if "searchJson" in response.url:
                try:
                    text = response.text()
                    data = json.loads(text)
                    api_data.append(data)
                except:
                    pass
                
        page.on("response", handle_response)
        
        # Busan WCONGNAMUL
        urlX = 972701
        urlY = 472471
        
        url = f"https://m.map.kakao.com/actions/searchView?q=%EC%B9%B4%ED%8E%98&wx={urlX}&wy={urlY}&level=4"
        page.goto(url)
        page.wait_for_timeout(3000)
        
        try:
            page.evaluate('document.querySelector(".link_more[data-type=\'place\']").click()')
            page.wait_for_timeout(3000)
        except Exception as e:
            pass
            
        try:
            page.evaluate('document.querySelector(".link_more[data-type=\'place\']").click()')
            page.wait_for_timeout(3000)
        except Exception as e:
            pass
            
        if api_data:
            print(f"Intercepted {len(api_data)} API responses.")
            with open("kakao_test_response.json", "w") as f:
                json.dump(api_data, f, ensure_ascii=False, indent=2)
            print("Saved to kakao_test_response.json")
            
        browser.close()

if __name__ == "__main__":
    test_m_kakao_json()
