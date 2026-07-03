# Railway deploy: hermes-agent base image + our brain CLI baked in.
# Persistent state (hermes config, brain DB, skills) lives on the Railway volume at /opt/data.
FROM nousresearch/hermes-agent:latest

COPY . /opt/brain-src
# Own venv (system python is PEP668-managed, and this keeps brain's deps out of hermes's env)
RUN uv venv /opt/brain-venv \
    && uv pip install --python /opt/brain-venv/bin/python /opt/brain-src \
    && ln -s /opt/brain-venv/bin/brain /usr/local/bin/brain

# One volume per Railway service, so the brain DB shares the hermes volume.
ENV BRAIN_DIR=/opt/data/brain

# The skill and config.toml are copied onto the volume at setup time (SETUP.md) —
# volumes are not mounted during build, so baking them here would be shadowed.
CMD ["gateway", "run"]
