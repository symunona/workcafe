## Data Processing Pipeline 
There are errors in the data pipeline.

We have the clean cafe data stored in `data/seoul/clean-data.db`, that we should be able to reset with `just db-clean`

- TASK 1: double check if that's what it does.

We are refining the algorhithm to merge cafes from different scraper sources.

Let's refine how it SHOULD work:

## Englihification
- create new field: llm_english
- iterate over all cafe in `data/seoul/cafedata.db`, populate each's llm_english name using locally hosted model ollama.
    - we do something like this already, but not sure, check if it's good enough quality with the current model

## Cleaning
0. assume that the scrapers are running constantly and there is also new data in `data/seoul/cafedata.db`
1. `just db-clean` should use fs cp to create `data/seoul/cafedata.db` -
2. from now on, always use the copy for read/write
3. clean the references that may be stored in the old DB first: we had the cafes and clean_cafe tables.
- let's rename cafes to scraped_cafes so it's cleaner.
- update the API endpoints to use this too in all layers from db wrapper through the API to the frontend
- the state should be: scraped_cafes are not linked, images should not be linked to the cafes yet (there is a consistent field over both cafes and images that should be clean)

TASK 2: 
- check current behavior
- adjust the components, so it does this!
- `just db-clean` should do these all!
- double check if it worked by creating checker scripts!
- separate/move data processing scripts under `data-processing/cleaner` folder


## Merging
`just merge-pipeline`

The goal: from the different sources (naver, osm, google, kakao) - we want to merge the cafes that are actually point to the same coffee shop.

The problems:
- location coordinates are not the exact same in each separate datasource
- naming: names are not the same, they can be korean OR english
- address may not be the same or even populated

Constraints:
- assume each platform contains the same shop only once -> one linked cafe can have one of each platform MAX.

Merge algo draft
    

- iterate over all `scraped_cafe`, ordered by scaping provider (kakao/naver/google/osm order) for each:
    - see, if we already have cafes in the clean_cafe db nearby the location, return neighbours and their distance, collect their english names
    - add similarity scores to each using levenstein distance containment
        - play with tresholds (e.g. if name exactly contains the other, assume that it's the same, like Starbucks Bangbae Coffeshop - and Starbucks within 50m: do heuristics and auto matching)
    - if something is NOT obvious, feed the local ollama LLM with a prompt to determine which one it thinks it belongs to giving it the option that it's a new one! (there can be multiple cafes in close proximity)
    - mind that we're starting with the per provider because kakao has the most cafes (probably false positives too, that are not cafes) - so at first, so that for the first group (kakao) - we do not actually have to run the LLMs, they'll all make a new clean_cafe entry, we only start merging from the second group (naver)

Before you start working, save the following data from the current clean_cafes, so you can test against if if the merger script worked:

- tests to make sure the data was good:
    - create a test-cleaning process by filtering out a small rect area 500m each direction from this cafe: https://workcafe.tmpx.space/cafe/da6525d8-8d64-4548-85ee-1c712a4153df
    - this should be merged together but currently it isnt:
        Starbucks 
            - from google: https://workcafe.tmpx.space/cafe/da6525d8-8d64-4548-85ee-1c712a4153df
            - from naver and kakao: https://workcafe.tmpx.space/cafe/239dcdb9-ca80-415d-bd7f-a63017144f69
        Compose Cafe
            - from google: https://workcafe.tmpx.space/cafe/07eb6c5b-4e8e-40de-93cc-3710c3e37361
            - from kakao and osm: https://workcafe.tmpx.space/cafe/4362d5cd-5ca6-4d8d-9f42-a4cc75cccd60
        This looks similar
            - google: https://workcafe.tmpx.space/cafe/89e89c2e-2e8b-422e-bf89-a66875b1e554
            - naver: https://workcafe.tmpx.space/cafe/0e309e64-ed65-460f-a808-bb866af31187
        The Coffee Bean
            - kakao & naver: https://workcafe.tmpx.space/cafe/dd5e46c5-cab7-4670-a558-a47f4f769505
            - osm: https://workcafe.tmpx.space/cafe/e73d6bcb-c13e-4758-9901-75a07576e4df

And there are more, but they may be a good starter!

So:
- run the cleaner for this area
- test if these are all merged togehter with the new algo


Break these down to reasonable tasks and get to it!