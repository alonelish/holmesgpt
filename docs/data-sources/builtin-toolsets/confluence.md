# Confluence

By enabling this toolset, HolmesGPT will be able to fetch Confluence pages. This is particularly useful if you store runbooks in Confluence and want Holmes to run investigations using these runbooks.

This toolset requires an [Atlassian API Key](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/){:target="_blank"}.

## Configuration

=== "Holmes CLI"

    Set the following environment variables and the Confluence toolset will be automatically enabled:

    ```bash
    export CONFLUENCE_USER="<Confluence username>"
    export CONFLUENCE_API_KEY="<Confluence API key>"
    export CONFLUENCE_BASE_URL="<Confluence base URL>"
    ```

    To test, run:

    ```bash
    holmes ask "why is my application failing? Get relevant runbooks from Confluence"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

    **Advanced Configuration:**

    You can also configure the toolset using a config file (`~/.holmes/config.yaml`):

    ```yaml
    toolsets:
        confluence:
            enabled: true
            config:
                base_url: "https://your-domain.atlassian.net"
                user: "<Confluence username>"
                api_key: "<Confluence API key>"
                verify_ssl: true  # Set to false to disable SSL certificate verification
    ```

=== "Robusta Helm Chart"

    **Helm Values:**

    ```yaml
    holmes:
        additionalEnvVars:
            - name: CONFLUENCE_USER
              value: <Confluence username>
            - name: CONFLUENCE_API_KEY
              value: <Confluence API key>
            - name: CONFLUENCE_BASE_URL
              value: <Confluence base URL>
        toolsets:
            confluence:
                enabled: true
    ```

    **Advanced Configuration with SSL verification disabled:**

    ```yaml
    holmes:
        toolsets:
            confluence:
                enabled: true
                config:
                    base_url: "https://your-domain.atlassian.net"
                    user: "<Confluence username>"
                    api_key: "<Confluence API key>"
                    verify_ssl: false
    ```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| fetch_confluence_url | Fetch a page in Confluence. Use this to fetch Confluence runbooks if they are present before starting your investigation. |
