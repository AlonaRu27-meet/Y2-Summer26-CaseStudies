import os
from anthropic import Anthropic
from dotenv import load_dotenv
from tavily import TavilyClient
from pypdf import PdfReader
import base64



load_dotenv()

client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
tavily_client = TavilyClient(api_key=os.getenv('TAVILY_API_KEY'))

try:
    with open("feedback.txt", "r") as changes_file:
        saved_changes = changes_file.read()
except FileNotFoundError:
    saved_changes = ""



system_message = """
    You are Dot, an intelligent Jerusalemite news and facts expert agent for "0202 - Points of View from Jerusalem". 
    Your aim is to take user-submitted news or articles, verify them using real-time internet search results, create a 1-minute summary, and provide a context-aware translation into Hebrew, Arabic, and English.
    
    Your ultimate job is to bridge cultural gaps, make localized information accessible, show opposing viewpoints neutrally, and prepare verified content ready for distribution to strategic partners (Educational Institutes, NGOs, and Influencers).

    Always:
    - Always be nice, pleasant, and act with deep empathy for cultural sensitivity.
    - Always provide a focused 1-minute summary in all three languages.
    - Always translate accurately while factoring in local context and political nuances.
    - List the exact verification sources and links found in the search context.

    Never:
    - DO NOT make up facts or links. If no sources are found, explicitly flag the story as unverified.
    - DO NOT take a political side; remain strictly factual and neutral.
    - DO NOT accept offensive, hateful, or violent language.

    Response format:
    You must ALWAYS respond using this exact format.
    - [Reliability Score & Sources]: Rate reliability (1-5) and list verified links.
    - [1-Minute Summary]: A brief, focused summary (In Hebrew, Arabic, and English).
    - [Full Context-Aware Translation]: The full translation tailored to the region's nuances.
    """
system_message = system_message + f"\nHere are past improvements you must remember:\n{saved_changes}"


def extract_text_from_pdf(pdf_path):

    reader = PdfReader(pdf_path)
    full_text = ""
    
    for page in reader.pages:
        full_text += page.extract_text() + "\n"
        
    return full_text

def search_internet(info):
    response = tavily_client.search(query=info, max_results=3)

    return response

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')



def run_chat():
    print('You: (type exit to quit)')
    history = []
    verified_stories = {}
    total_con_tokens = 0
    reply = ""
    user_input = input("Paste text, type a PDF (e.g., article.pdf), OR type an image name (e.g., photo.jpg):\n>> ")
    while True:

        turn_number = int(len(history) / 2) + 1
        print(f"[Turn {turn_number}] ", end="")

        

        if user_input.startswith('/get '):
            story_name = user_input.replace('/get ', '')
            if story_name in verified_stories:
                print(verified_stories[story_name])
            else:
                print(f"Sorry, you don't have a story named '{story_name}' in your favorites.")
            continue

        if user_input.lower() == 'exit':
            while True:
                user_rating = input("\nHow would you rate the agent's verification quality? (1-5): ").strip()
                if user_rating in ['1', '2', '3', '4', '5']:
                    print(f"Thank you for your feedback! You rated us: {user_rating}/5")
                    if int(user_rating) < 4:
                        changes = input("Can you tell us what was wrong? ")
                        with open("feedback.txt", "a") as changes_file:
                            changes_file.write(changes + "\n")
                        print("Thank you! we will try to improve next time")

                    break
                else:
                    print("Invalid input. Please enter a number between 1 and 5.")
            
            if 'reply' in locals() or 'reply' in globals():
                comment = input("\nWould you like to add this final story to 'verified stories'? (yes/no): ").strip().lower()
                if comment == "yes":

                    name = input("How would you like to name this story? ").strip()
                    verified_stories[name] = reply

                    with open("verified_stories.txt", "a") as fav_file:
                        fav_file.write(f"=== Story Name: {name} ===\n")
                        fav_file.write(reply)
                        fav_file.write("\n\n---------------------------------------\n\n")
                    print(f"Story '{name}' saved successfully!")

            break



        if user_input.lower() == 'reset':
            history = []
            print("Conversation history cleared. Starting fresh!")
            continue

        if user_input.lower() == '/summary':
            print("\n--- Dot is reviewing your chat history... ---")
            
            summary_response = client.messages.create(
                model='claude-3-haiku-20240307',
                max_tokens=300,
                temperature=0.9,
                system="You are a helpful tutor. Summarize the user's progress based on the history.",
                messages=history + [{'role': 'user', 'content': 'Please give a quick, structured summary of what we discussed so far and what I learned.'}]
            )
            
            reply_text = summary_response.content.text
            print(f"\n[Summary]: {reply_text}\n")

            continue

        if turn_number == 1:
                user_input = input("Paste text, type a PDF (e.g., article.pdf), OR type an image name (e.g., photo.jpg):\n>> ")
                print(f"Processing your input...\n")
                    
                if user_input.lower().endswith(('.jpg', '.jpeg', '.png')):
                    base64_image = encode_image(user_input)
                    media_type = "image/jpeg" if user_input.lower().endswith(('.jpg', '.jpeg')) else "image/png"
                        
                    user_input = [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64_image}},
                        {"type": "text", "text": "Please extract the text from this image and verify its reliability."}
                    ]
                    
                elif user_input.lower().endswith('.pdf'):
                    story_text = extract_text_from_pdf(user_input) 
                    search_results = search_internet(story_text)    
                    user_input = f"User Story:\n{story_text}\n\nInternet Search Results:\n{search_results}"
                    
                else:
                    search_results = search_internet(user_input)  
                    user_input = f"User Story:\n{user_input}\n\nInternet Search Results:\n{search_results}"

        else:
            user_input = input('>> ')
    
        

    history.append({'role': 'user', 'content': user_input})
        #print('History:', history)

    response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=3000, #number of chars
            temperature=0.7, #creativity level
            system=system_message,
            messages=history
        )
    reply = response.content.text
    print(f'Claude: {reply}')
    history.append({'role': 'assistant', 'content': reply})

        
    in_tokens = response.usage.input_tokens
    out_tokens = response.usage.output_tokens

    total_tokens = in_tokens + out_tokens

    print(f"[Tokens used — In: {in_tokens} | Out: {out_tokens} | Total: {total_tokens}]")

    total_con_tokens += total_tokens
    print (f"[Running Total: {total_con_tokens} tokens]")

    input_cost = (in_tokens / 1000000) * 0.25
    output_cost = (out_tokens / 1000000) * 1.25
    cost_in_cents = (input_cost + output_cost) * 100
    print(f"[Estimated Cost: {cost_in_cents:.5f}¢]")

if __name__ == "__main__":
    run_chat()

