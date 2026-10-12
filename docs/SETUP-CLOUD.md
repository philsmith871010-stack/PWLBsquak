# Cloud setup checklist (for a local Claude Code session)

Paste the block below into a Claude Code session running in `~/pwlbsquawk` on the Mac.
It uses `gh` (GitHub CLI, `brew install gh` then `gh auth login` if needed). Steps that need
a human click are marked.

```
Set up the hosted squawk feed for this repo (philsmith871010-stack/PWLBsquak) following docs/INTEGRATION.md:

1. Open a pull request from claude/affectionate-volta-gru332 into main and merge it (gh pr create, gh pr merge --merge).
2. Leave this repo's visibility as it is. It is public so its Actions minutes are free; making it private brings back the 2,000-minute monthly cap (see the notes in docs/INTEGRATION.md). Ask me before changing it.
3. Create an empty public repo philsmith871010-stack/pwlb-squawk-feed (gh repo create --public) with an initial commit containing a README, and enable GitHub Pages on it from branch main, folder / (gh api -X POST repos/philsmith871010-stack/pwlb-squawk-feed/pages -f build_type=legacy -f 'source[branch]=main' -f 'source[path]=/').
4. Create an SSH deploy key: ssh-keygen -t ed25519 -N "" -f /tmp/pages_key; add /tmp/pages_key.pub as a write-enabled deploy key on the public repo (gh repo deploy-key add /tmp/pages_key.pub -R philsmith871010-stack/pwlb-squawk-feed --allow-write --title squawk); add the private key as the secret PAGES_DEPLOY_KEY on this repo (gh secret set PAGES_DEPLOY_KEY < /tmp/pages_key); delete both key files.
5. Set the repository variable PAGES_REPO=philsmith871010-stack/pwlb-squawk-feed (gh variable set PAGES_REPO --body ...).
6. Tell me to run `claude setup-token` myself; when I paste nothing and say it is copied, run: gh secret set CLAUDE_CODE_OAUTH_TOKEN (it reads from the clipboard via pbpaste: pbpaste | gh secret set CLAUDE_CODE_OAUTH_TOKEN). Never print the token.
7. Trigger the workflow (gh workflow run squawk.yml --ref main), wait for it (gh run watch), and show me the last 30 lines of the "Poll and write" step if it fails.
8. Report the Pages URL: https://philsmith871010-stack.github.io/pwlb-squawk-feed/
```
