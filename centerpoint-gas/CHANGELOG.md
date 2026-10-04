# Changelog

## 0.4.13

- Bug fixes for login issues. 

## 0.4.6

- New optional `delete_2fa_email` config option (default `false`): moves the verification-code email to Gmail's Trash once its code has actually been used. "Used" is defined as the existing post-submit wait succeeding rather than merely having read the code out of the inbox, so a failed or rejected submission still leaves the email available for the retry path to re-read. 
- Fixed a latent bug surfaced by the above: `_search_gmail_for_code`'s early "no messages at all" return path still returned a bare `None` after the function started returning a `(code, message_id)` pair, which would have raised a `TypeError` on unpack the first time an IMAP search came back empty.


## 0.4.3

- Cherry-picked two fixes from a community fork (RedNetBaron/home-assistant-apps), skipping that fork's cost-tracking feature for now: (1) `_B2C_REDIRECT_URI` simplified from `.../myaccounts/Index` to the bare `myaccount.centerpointenergy.com` domain root -- very likely why every run all session has logged a harmless-but-noisy `HTTP 404` warning on that specific sub-path. (2) Added `_page_is_blank`, checked alongside `_needs_login`: a reused session that's expired server-side may render a totally blank account-home page instead of redirecting to login, which `_needs_login` alone wouldn't catch -- a plausible (not yet directly observed) explanation for the same "couldn't find View Usage link" failure mode this add-on has hit for other reasons already. Low-risk hedge, adopted as a precaution.