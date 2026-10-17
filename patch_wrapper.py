import re

with open("infra/deploy/deploy_agent_engine.py", "r") as f:
    code = f.read()

old_block = """            from godview.agents import create_agent_app
            from godview.agents.server import AgentExecutor
            executor = AgentExecutor(base_url=payload.deployment_spec.env_vars["GODVIEW_SOVEREIGN_URL"])
            app_instance = create_agent_app(executor=executor)"""

new_block = """            from godview.agents.server import AgentExecutor
            executor = AgentExecutor(base_url=payload.deployment_spec.env_vars["GODVIEW_SOVEREIGN_URL"])
            
            class CourtAgentWrapper:
                def __init__(self, ex):
                    self._ex = ex
                def set_up(self): pass
                def query(self, **kwargs) -> dict:
                    return {}
            
            app_instance = CourtAgentWrapper(executor)"""

if old_block in code:
    with open("infra/deploy/deploy_agent_engine.py", "w") as f:
        f.write(code.replace(old_block, new_block))
    print("Patched successfully.")
else:
    print("Old block not found.")
