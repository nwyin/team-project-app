# Railway deploy: hermes-agent base image + our brain CLI baked in.
# Persistent state (hermes config, brain DB, skills) lives on the Railway volume at /opt/data.
FROM nousresearch/hermes-agent:latest

COPY . /opt/brain-src
RUN uv pip install --system /opt/brain-src

# One volume per Railway service, so the brain DB shares the hermes volume.
ENV BRAIN_DIR=/opt/data/brain

# The skill and config.toml are copied onto the volume at setup time (SETUP.md) —
# volumes are not mounted during build, so baking them here would be shadowed.
