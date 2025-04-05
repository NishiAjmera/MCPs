import asyncio
# import types
from mcp import server
from mcp.types import Tool
from typing import Dict, Any, Optional, List
import httpx
import sqlite3
import json
from datetime import datetime
from mcp.server.fastmcp import FastMCP
import requests
from pydantic import BaseModel, Field

mcp = FastMCP("tools_server")

class WebSearchAPIWrapper:
    def search(self, query: str, authorization_token: str, request_headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Makes a POST request to the remote Exa search API."""
        try:
            response = requests.post(
                "",
                json={"query": query},
                headers={
                    "Authorization": "",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Exa search error: {e}")
            return None

    def run(self, query: str, authorization_token: str, request_headers: Dict[str, str]) -> str:
        response_data = self.search(
            query=query,
            authorization_token=authorization_token,
            request_headers=request_headers,
        )
        if response_data is not None and response_data.get("answer"):
            result = {
                "function": "live_search",
                "data": {"results": response_data.get("answer"), "query": query},
            }
            return json.dumps(result)
        return "Failed to fetch results from Live search API."

@mcp.tool()
async def live_search(
    query: str
) -> str:
    """
    A tool for performing online web searches using EXA Search API.
    Useful for finding current information from the web.
    
    Args:
        query: Search query string to look up online

    Returns:
        Search results with preserved URLs and source links
    """
    api_wrapper = WebSearchAPIWrapper()

    print("here:::")
    
    result = api_wrapper.run(
        query=query,
        authorization_token=authorization_token,
        request_headers=request_headers
    )
    
    return result

@mcp.tool()
async def get_info(
    category: str,
    fields: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Return JSON information for different categories.
    
    Args:
        category: Information category ('weather', 'stocks', 'news')
        fields: Optional list of specific fields to return
        
    Returns:
        JSON object with requested information
    """
    # Sample information database
    info_database = {
        "weather": {
            "temperature": 72,
            "condition": "sunny",
            "humidity": 45,
            "wind_speed": 10,
            "location": "London"
        },
        "stocks": {
            "AAPL": 150.25,
            "GOOGL": 2800.75,
            "MSFT": 285.30,
            "AMZN": 3300.50
        },
        "news": {
            "headlines": [
                "Breaking: Important Event Happened",
                "Technology: New Innovation Announced",
                "Sports: Team Wins Championship"
            ],
            "sources": ["Reuters", "AP", "Bloomberg"],
            "updated_at": datetime.now().isoformat()
        }
    }
    
    if category not in info_database:
        return {"error": f"Category '{category}' not found"}
    
    if fields:
        result = {
            field: info_database[category].get(field)
            for field in fields
            if field in info_database[category]
        }
    else:
        result = info_database[category]
    
    return result

@mcp.tool()
async def update_state(self,
                          state_name: str,
                          new_value: str) -> Dict[str, Any]:
        """
        Update state in the database.
        
        Args:
            state_name: Name of the state to update
            new_value: New value to set
            
        Returns:
            Dictionary with update status and information
        """
        try:
            conn = sqlite3.connect('tools_state.db')
            cursor = conn.cursor()
            
            # Check if state exists
            cursor.execute(
                "SELECT id FROM states WHERE name = ?",
                (state_name,)
            )
            result = cursor.fetchone()
            
            if result:
                # Update existing state
                cursor.execute("""
                    UPDATE states 
                    SET value = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                """, (new_value, state_name))
            else:
                # Insert new state
                cursor.execute("""
                    INSERT INTO states (name, value)
                    VALUES (?, ?)
                """, (state_name, new_value))
            
            conn.commit()
            
            # Get updated record
            cursor.execute("""
                SELECT name, value, updated_at
                FROM states
                WHERE name = ?
            """, (state_name,))
            
            updated_state = cursor.fetchone()
            conn.close()
            
            return {
                "status": "success",
                "state": {
                    "name": updated_state[0],
                    "value": updated_state[1],
                    "updated_at": updated_state[2]
                }
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to update state: {str(e)}"
            }

async def close(self):
    """Cleanup resources"""
    await self.http_client.aclose()

if __name__ == "__main__":
    mcp.run(transport="sse")
