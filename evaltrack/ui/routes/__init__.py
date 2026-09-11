"""One router factory per group of endpoints. Each takes what its routes need
and closes over it, the way `create_app` does."""

# Page size only. Skipping still reads each skipped run, so an offset cap
# bounds nothing.
MAX_PAGE = 500
