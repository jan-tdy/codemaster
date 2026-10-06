# Community catalog

Anyone can submit an app to Code Master's Store from here — your app does
not need to belong to `jan-tdy`, and it does not need to be released or
even touched again after merging. Code Master reads every `*.json` file in
this folder directly (one file per submission) and lists the apps it
describes, right alongside the jan-tdy-published ones, tagged with a
**community** badge.

## How it works

1. Your app lives in **your own** GitHub repository, however you like.
2. You add one file here, `catalog/<your-app-id>.json`, shaped exactly
   like a [`codemaster-metadata.json`](../README.md#publishing-an-app-codemaster-metadatajson)
   — because that's exactly what it is, just hosted in this repo instead
   of in yours:

   ```json
   {
     "schema_version": 1,
     "publisher": "your-github-username",
     "repo": "your-repo-name",
     "branch": "main",
     "homepage": "https://github.com/your-github-username/your-repo-name",
     "apps": [
       {
         "id": "your-app-id",
         "name": "Your App",
         "tagline": "One line about what it does",
         "description": "A longer paragraph shown on the app's details page.",
         "category": "Utilities",
         "version": "1.0",
         "author": "Your Name",
         "icon": "icon.png",
         "subdir": ".",
         "entrypoint": "main.py",
         "run": "python3 main.py",
         "requirements": "requirements.txt",
         "update_method": "sync",
         "maintained": true
       }
     ]
   }
   ```

3. Open a pull request against this repository adding that one file.

Every field means exactly what it means in the main README's metadata
reference, with one addition: `branch` (top-level, or per-app) names the
branch of **your** repo that `repo`/`icon`/`run`/etc. refer to — it
defaults to your repo's default branch. (jan-tdy's own apps don't need
this because for those, the metadata branch *is* the code branch; a
community submission's metadata lives here instead, so the two need not
match.)

## What installing a community app actually does

Code Master clones `https://github.com/<publisher>/<repo>.git` and, when
you tell it to, runs the `run` command you declared — on the installing
user's own computer, with their own user permissions. Code Master shows a
one-time warning before installing any community app for exactly this
reason. Review pull requests here the same way you'd review a dependency:
read what `run` actually launches, and don't merge a submission whose
`run` command, `requirements`, or linked repository you haven't looked at.

## Limits of this first version

- No private repos — `publisher`/`repo` must be public.
- No update probing beyond what the main scan already does for any
  `sync`/`release` app: Code Master compares the latest commit (or
  release tag) on your declared branch against what a user has installed.
