import re

with open("infra/deploy/deploy_agent_engine.py", "r") as f:
    code = f.read()

old_block = """        # Dynamically import the court agent app factory from godview.agents.<agent_type>
        module_name = f"godview.agents.{payload.agent_type}.agent"
        try:
            agent_mod = __import__(module_name, fromlist=["create_agent_app"])
            app_instance = agent_mod.create_agent_app(
                agent_id=payload.agent_id,
                subject_key=payload.subject_key,
                sovereign_url=payload.deployment_spec.env_vars["GODVIEW_SOVEREIGN_URL"],
            )
        except ImportError as exc:
            raise RuntimeError(
                f"Court agent module '{module_name}' is not yet available (Wave 3 deliverable): {exc}"
            ) from exc"""

new_block = """        try:
            from godview.agents import create_agent_app
            from godview.agents.server import AgentExecutor
            executor = AgentExecutor(base_url=payload.deployment_spec.env_vars["GODVIEW_SOVEREIGN_URL"])
            app_instance = create_agent_app(executor=executor)
        except ImportError as exc:
            raise RuntimeError(f"Wave 3 agents not available: {exc}") from exc"""

if old_block in code:
    with open("infra/deploy/deploy_agent_engine.py", "w") as f:
        f.write(code.replace(old_block, new_block))
    print("Patched successfully.")
else:
    print("Old block not found.")
