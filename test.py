from openai import OpenAI

client = OpenAI(
    api_key='sk-08238190401b4bf59f63f8e4658c35c1',
    base_url="https://api.deepseek.com")

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "user", "content": "Hi"},
    ],
    max_tokens=1,
    stream=False,
)

print("连接成功:", response.choices[0].message.content)
