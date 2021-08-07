# boto3-patterns

Small, runnable boto3 examples of the patterns every AWS script needs: pagination, retries, waiters, concurrency and assumed roles.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A set of runnable example scripts.

**The problem it solves:** Most AWS scripts get pagination, throttling and retries subtly wrong, and the right patterns are scattered across documentation.

## Who it is for

Python developers writing scripts and services against AWS.

## How it will work

1. Each pattern is one short, commented file.
2. You run it against stubbed responses or your own account.
3. You copy the pattern into your code.

## Planned features

Unticked items are not built yet.

- [ ] Pagination done correctly
- [ ] Retry and backoff configuration
- [ ] Waiters and custom waiters
- [ ] Safe concurrency with threads
- [ ] Cross-account access with assumed roles
- [ ] Testing with stubbed clients, no AWS account needed

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```bash
python patterns/pagination.py
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
