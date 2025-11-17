---
description: Understand Groq API rate limits, headers, and best practices for managing request and token quotas in your applications.
title: Rate Limits - GroqDocs
image: https://console.groq.com/og_cloudv5.jpg
---

# Rate Limits

Rate limits act as control measures to regulate how frequently users and applications can access our API within specified timeframes. These limits help ensure service stability, fair access, and protection against misuse so that we can serve reliable and fast inference for all.

## [Understanding Rate Limits](#understanding-rate-limits)

Rate limits are measured in:

* **RPM:** Requests per minute
* **RPD:** Requests per day
* **TPM:** Tokens per minute
* **TPD:** Tokens per day
* **ASH:** Audio seconds per hour
* **ASD:** Audio seconds per day

[Cached tokens](https://console.groq.com/docs/prompt-caching) do not count towards your rate limits.

Rate limits apply at the organization level, not individual users. You can hit any limit type depending on which threshold you reach first.

**Example:** Let's say your RPM = 50 and your TPM = 200K. If you were to send 50 requests with only 100 tokens within a minute, you would reach your limit even though you did not send 200K tokens within those 50 requests.

## [Rate Limits](#rate-limits)

The following is a high level summary and there may be exceptions to these limits. You can view the current, exact rate limits for your organization on the [limits page](https://console.groq.com/settings/limits) in your account settings.

**Need higher rate limits?** Upgrade to [Developer tier](https://console.groq.com/settings/billing/plans) to access higher limits, [Batch](https://console.groq.com/docs/batch) and [Flex](https://console.groq.com/docs/flex-processing) processing, and more.

| Free Tier LimitsDeveloper Tier Limits |     |     |     |     |     |     |
| ------------------------------------- | --- | --- | --- | --- | --- | --- |
| MODEL ID                              | RPM | RPD | TPM | TPD | ASH | ASD |

| allam-2-7b                                    | 30300 | 7K60K     | 6K60K   | 500K\- | \-       | \-      |
| --------------------------------------------- | ----- | --------- | ------- | ------ | -------- | ------- |
| groq/compound                                 | 30200 | 25020K    | 70K200K | \-     | \-       | \-      |
| groq/compound-mini                            | 30200 | 25020K    | 70K200K | \-     | \-       | \-      |
| llama-3.1-8b-instant                          | 301K  | 14.4K500K | 6K250K  | 500K\- | \-       | \-      |
| llama-3.3-70b-versatile                       | 301K  | 1K500K    | 12K300K | 100K\- | \-       | \-      |
| meta-llama/llama-4-maverick-17b-128e-instruct | 301K  | 1K500K    | 6K300K  | 500K\- | \-       | \-      |
| meta-llama/llama-4-scout-17b-16e-instruct     | 301K  | 1K500K    | 30K300K | 500K\- | \-       | \-      |
| meta-llama/llama-guard-4-12b                  | 30100 | 14.4K50K  | 15K30K  | 500K1M | \-       | \-      |
| meta-llama/llama-prompt-guard-2-22m           | 30100 | 14.4K50K  | 15K30K  | 500K\- | \-       | \-      |
| meta-llama/llama-prompt-guard-2-86m           | 30100 | 14.4K50K  | 15K30K  | 500K\- | \-       | \-      |
| moonshotai/kimi-k2-instruct                   | 601K  | 1K500K    | 10K250K | 300K\- | \-       | \-      |
| moonshotai/kimi-k2-instruct-0905              | 601K  | 1K500K    | 10K250K | 300K\- | \-       | \-      |
| openai/gpt-oss-120b                           | 301K  | 1K500K    | 8K250K  | 200K\- | \-       | \-      |
| openai/gpt-oss-20b                            | 301K  | 1K500K    | 8K250K  | 200K\- | \-       | \-      |
| openai/gpt-oss-safeguard-20b                  | 301K  | 1K500K    | 8K150K  | 200K\- | \-       | \-      |
| playai-tts                                    | 10250 | 100100K   | 1.2K50K | 3.6K2M | \-       | \-      |
| playai-tts-arabic                             | 10250 | 100100K   | 1.2K50K | 3.6K2M | \-       | \-      |
| qwen/qwen3-32b                                | 601K  | 1K500K    | 6K300K  | 500K\- | \-       | \-      |
| whisper-large-v3                              | 20300 | 2K200K    | \-      | \-     | 7.2K200K | 28.8K4M |
| whisper-large-v3-turbo                        | 20400 | 2K200K    | \-      | \-     | 7.2K400K | 28.8K4M |

## [Rate Limit Headers](#rate-limit-headers)

In addition to viewing your limits on your account's [limits](https://console.groq.com/settings/limits) page, you can also view rate limit information such as remaining requests and tokens in HTTP response headers as follows:

The following headers are set (values are illustrative):

| Header                         | Value    | Notes                                    |
| ------------------------------ | -------- | ---------------------------------------- |
| retry-after                    | 2        | In seconds                               |
| x-ratelimit-limit-requests     | 14400    | Always refers to Requests Per Day (RPD)  |
| x-ratelimit-limit-tokens       | 18000    | Always refers to Tokens Per Minute (TPM) |
| x-ratelimit-remaining-requests | 14370    | Always refers to Requests Per Day (RPD)  |
| x-ratelimit-remaining-tokens   | 17997    | Always refers to Tokens Per Minute (TPM) |
| x-ratelimit-reset-requests     | 2m59.56s | Always refers to Requests Per Day (RPD)  |
| x-ratelimit-reset-tokens       | 7.66s    | Always refers to Tokens Per Minute (TPM) |

## [Handling Rate Limits](#handling-rate-limits)

When you exceed rate limits, our API returns a `429 Too Many Requests` HTTP status code.

**Note**: `retry-after` is only set if you hit the rate limit and status code 429 is returned. The other headers are always included.