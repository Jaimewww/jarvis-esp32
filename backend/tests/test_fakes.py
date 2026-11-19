import asyncio


def test_fake_agent_yields_configured_chunks():
    from app.fakes import FakeAgent

    agent = FakeAgent(chunks=["Hola, ", "señor."])

    async def collect():
        return [chunk async for chunk in agent.reply("qué hora es", turn_id="t1")]

    assert asyncio.run(collect()) == ["Hola, ", "señor."]
    assert agent.calls == [("qué hora es", "t1")]
