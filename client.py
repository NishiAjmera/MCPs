import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from typing import Dict, Any, List, Tuple, TypedDict, Annotated
import json
import asyncio
from urllib.parse import urlparse
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.tools import StructuredTool
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents.format_scratchpad import format_to_openai_functions
from langchain.agents.output_parsers import OpenAIFunctionsAgentOutputParser
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from contextlib import asynccontextmanager

class MCPSDKClient:
    def __init__(self, server_url: str):
        """
        Initialize the MCP SDK client.
        
        Args:
            server_url: Full URL to SSE endpoint (e.g. http://localhost:8000/sse)
        """
        if urlparse(server_url).scheme not in ("http", "https"):
            raise ValueError("Server URL must start with http:// or https://")
            
        self.server_url = server_url
        self.session: ClientSession = None
        self.streams = None
        self.tools = []
        self._context = None
        
    @asynccontextmanager
    async def _get_session(self):
        """Create and manage the SSE client session."""
        async with sse_client(self.server_url) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                yield session, streams

    async def connect(self) -> None:
        """Connect to the MCP server and initialize session."""
        try:
            self._context = self._get_session()
            session, streams = await self._context.__aenter__()
            self.session = session
            self.streams = streams
            
            await self.session.initialize()
            print(f"Connected to MCP server at {self.server_url}")
            
            # Get available tools
            tools_result = await self.session.list_tools()
            print("tools_result", tools_result)
            self.tools = [{
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.inputSchema
                    } for tool in tools_result.tools]
            # self.tools = [tool.name for tool in tools_result.tools]
            # print(f"Available tools: {self.tools}")
            
        except Exception as e:
            print(f"Failed to connect to MCP server: {str(e)}")
            if self._context:
                await self._context.__aexit__(None, None, None)
            raise

    async def list_tools(self) -> List[str]:
        """Return the list of available tools."""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")
        return self.tools

    async def call_tool(self, tool_name: str, **kwargs) -> Any:
        """
        Call a specific tool on the server.
        
        Args:
            tool_name: Name of the tool to call
            **kwargs: Arguments to pass to the tool
            
        Returns:
            Tool execution result
        """
        if not self.session:
            raise RuntimeError("Not connected to MCP server")
        
        try:
            print("tool_name, kwargs", tool_name, kwargs)
            result = await self.session.call_tool(tool_name, kwargs)
            print("result:::", result)
            # Handle both direct results and results with data attribute
            if hasattr(result, 'data'):
                return result.data
            return result
        except Exception as e:
            print(f"Failed to call tool {tool_name}: {str(e)}")
            return {"error": str(e)}

    async def close(self) -> None:
        """Close the connection to the server."""
        if self._context:
            await self._context.__aexit__(None, None, None)
            self.session = None
            self.streams = None
            self._context = None

class MCPLangGraphAgent:
    def __init__(self, server_url: str, openai_api_key: str):
        """
        Initialize the LangGraph agent with MCP integration.
        
        Args:
            server_url: URL of the MCP server
            openai_api_key: OpenAI API key for the LLM
        """
        # Initialize MCP client
        self.mcp_client = MCPSDKClient(server_url)
        
        # Initialize LLM
        self.llm = ChatOpenAI(
            model="gpt-4o",
            openai_api_key=openai_api_key,  # type: ignore[call-arg])
        
        # These will be initialized during setup
        self.mcp_tools = []
        self.tools = []
        self.agent_executor = None
        self.workflow = None

    async def setup(self):
        """Setup the agent by connecting to MCP and initializing tools."""
        # Connect to MCP and get tools
        await self.mcp_client.connect()
        self.mcp_tools = await self.mcp_client.list_tools()
        # print("self.mcp_tools",self.mcp_tools)
        # Create LangChain tools
        self.tools = await self._create_langchain_tools()
        # print("self.tools",self.tools)
        
        # Initialize the agent
        self.agent_executor = self._create_agent_executor()
        
        # Create the LangGraph workflow
        self.workflow = self._create_workflow()
    
    async def _create_langchain_tools(self) -> List[StructuredTool]:
        """Convert MCP tools to LangChain tools."""
        tools = []
        for tool in self.mcp_tools:
            tool_name = tool['name']
            description = tool['description']
            print("tool",tool)
            print("input_schema=  ",tool['input_schema'])
            
            async def _run_tool(input: str = "", tn=tool_name, **kwargs) -> Any:
                """Async function to run the tool"""
                if input:
                    kwargs['input'] = input
                return await self.mcp_client.call_tool(tn, **kwargs)

            def _sync_run(input: str = "", tn=tool_name, **kwargs) -> Any:
                """Sync wrapper for the async function"""
                loop = asyncio.get_event_loop()
                return loop.run_until_complete(_run_tool(input, tn=tn, **kwargs))

            tools.append(
                StructuredTool(
                    name=tool_name,
                    args_schema=tool['input_schema'],
                    func=_sync_run,
                    coroutine=_run_tool,
                    description=description
                )
            )
        return tools
    
    def _create_agent_executor(self) -> AgentExecutor:
        """Create the LangChain agent executor."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a helpful AI assistant with access to various tools through MCP."),
            ("human", "{input}"),
            ("system", "Think through this step-by-step:\n1) What tools do I have available?\n2) What tool would be most helpful?\n3) How should I use it?"),
            MessagesPlaceholder(variable_name="agent_scratchpad")
        ])
        
        print("self.tools",self.tools)
        # Create the agent with proper tools formatting
        agent = create_openai_tools_agent(
            llm=self.llm,
            tools=self.tools,
            prompt=prompt
        )
        
        # Create the executor with proper async handling
        return AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=True,
            handle_parsing_errors=True
        )
    
    def _create_workflow(self) -> StateGraph:
        """Create the LangGraph workflow."""
        # Define the state schema
        class AgentState(TypedDict):
            input: str
            output: str | None

        # Create workflow with state schema
        workflow = StateGraph(AgentState)
        
        # Define the agent function
        async def agent_node(state: AgentState) -> AgentState:
            """Execute the agent on the current state."""
            result = await self.agent_executor.ainvoke({"input": state["input"]})
            return {"input": state["input"], "output": result["output"]}
        
        # Add nodes to the graph
        workflow.add_node("agent", agent_node)
        
        # Define edges
        workflow.add_edge("agent", END)
        
        # Set entry point
        workflow.set_entry_point("agent")
        
        return workflow.compile()
    
    async def run(self, user_input: str) -> Dict[str, Any]:
        """
        Run the agent workflow with user input.
        
        Args:
            user_input: The user's query or request
            
        Returns:
            Dict containing the agent's response
        """
        if not self.workflow:
            await self.setup()
            
        initial_state = {"input": user_input, "output": None}
        result = await self.workflow.ainvoke(initial_state)
        return result
    
    async def close(self):
        """Close the agent and its connections."""
        if self.mcp_client:
            await self.mcp_client.close()

# Example usage
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv()
    
    async def main():
        # Initialize agent
        agent = MCPLangGraphAgent(
            server_url="http://localhost:8000/sse",
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        
        try:
            # Run a test query
            result = await agent.run("What is the latest news of apple ?")
            print("Agent response:", result)
        finally:
            # Ensure proper cleanup
            await agent.close()
    
    asyncio.run(main()) 
