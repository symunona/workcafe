# Work Cafe Map
I want a map that has all the cafes independently searchable for a particular city - not influenced by how much they've been paying for google or whatever platform.

I want to be able to scrape any city, then put my findings on a map, then later run image classification of their pics of these places so I can determine a few things e.g. if they laptops (peeps working), high chairs and tables or outlets.

I want to also scrape images from the outside e.g. streetview, to have a look at the place from the outside (V2)

## Scraping and data collection

I want to scrape a db for cafes, using online databases like:
- google maps
- open street maps
- kakao maps
- naver maps
- 4square

1. I want to save all the data I find about each cafe per provider at first:
	- platform
	- link, url on platform
	- link, url they given (on google there are many times referred wep page)
	- name
	- address
	- exact spacial location
	- any other metadata we can capture from a platform

2. I want to get/save the pictures that are present for each.
		Save metadata for each pic, like their orig url, their location data if present (so we can do easy filtering later) extract exif data and put that in a searhable format.

I am thinking a simple sqlite db for the scraper for regular backups.

## Ingest

I want to merge data sources smartly: dedupe them if they're the same place (probably doing a levenstein distance on the name, second, doing a simple cheap e.g. gemini llm call to a cheap/simple model to cast if they're likely the same place or separate by feeding just the metadata from each source )

Merge all the data, and make it a unified schema for representation and filtering.
## Display
Frontend, simple react app.
Filters up top.

First, I am thinking of hosting it on my VPS or github pages. 
Frontend app statically hosted.

At first the data dowloaded for the current city in e.g. compressed JSON format (just cafe list with metadata - hosted statically) - then do frontend searching.
Maybe I'll host it later on a server for e
Data hosted through github pages first.

Separate repo for the actual data. Where do ppl host data like this? I assume it'd be some GB per city...

Let's go first simple:
Statically hosted leaflet map.

## Image storage pattern

All scrapers and tools **must** follow this layout — do not deviate:

```
data/seoul/{provider}/{provider_id}/images/img_0.jpg
data/seoul/{provider}/{provider_id}/images/img_1.jpg
...
```

The `metadata` column in the DB must contain:
```json
{ "local_images": ["/images/{provider}/{url_encoded_provider_id}/images/img_0.jpg", ...] }
```

Rules:
- Images always go in a subdirectory named `images/` inside the cafe dir.
- Filenames are `img_0.ext`, `img_1.ext`, … (sequential, zero-indexed).
- `local_images` stores **URL paths** (starts with `/images/`), never CDN URLs and never filesystem paths.
- `provider_id` in the URL path must be percent-encoded (`urllib.parse.quote(provider_id, safe='')`).
- Never store CDN URLs in `local_images` — that field is exclusively for locally-served paths.
- Run `scraper/sync_local_images.py` after any scrape run to repair/rebuild `local_images` from disk. It is idempotent and safe to run at any time.
