# 🤖 OpenCastor Community Recipes

**Working robot configs shared by the community.**

Each recipe is a tested, PII-scrubbed config with documentation that someone got working on real hardware. Browse them, learn from them, use them as starting points.

## How to Use

```bash
# Browse all recipes
castor hub browse

# Search for something specific
castor hub search "outdoor patrol"

# Filter by category
castor hub browse --category home --difficulty beginner

# View details
castor hub show <recipe-id>

# Install a recipe to your project
castor hub install <recipe-id>
```

## How to Contribute

1. Get your robot working with OpenCastor
2. Package your config: `castor hub share --config robot.rcan.yaml --docs BUILD.md`
3. Review the scrubbed output — make sure no secrets leaked
4. Edit the generated README with tips, photos, and lessons learned
5. Submit a PR adding your `recipe-*` folder here

### What makes a great recipe?

- **Tested on real hardware** — not just theoretical
- **Clear use case** — what does this robot actually do?
- **Honest difficulty rating** — don't undersell the complexity
- **Lessons learned** — what didn't work? What would you change?
- **Photos or video links** — show, don't just tell
- **Budget breakdown** — help others plan their builds

### PII Scrubbing

The `castor hub share` command automatically removes:
- API keys and tokens
- Email addresses and phone numbers
- Public IP addresses (private ranges are kept)
- WiFi SSIDs and passwords
- Home directory paths
- Hostnames

Always review the output before submitting.

## Categories

| Category | Description |
|----------|-------------|
| `home` | Home & Indoor — vacuuming, monitoring, pet interaction |
| `outdoor` | Outdoor & Exploration — terrain navigation, mapping |
| `service` | Service & Hospitality — delivery, greeting, guidance |
| `industrial` | Industrial & Manufacturing — inspection, assembly |
| `education` | Education & Research — teaching, experiments |
| `agriculture` | Agriculture & Farming — crop monitoring, weed detection |
| `security` | Security & Surveillance — patrol, anomaly detection |
| `companion` | Companion & Social — interactive, conversational |
| `art` | Art & Creative — drawing, performance, installation |
| `custom` | Custom / Other — everything else |

## License

All recipes are shared under [Apache 2.0](../LICENSE), same as OpenCastor.
By submitting a recipe, you agree to this license.
