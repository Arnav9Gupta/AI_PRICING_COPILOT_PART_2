import streamlit as st
from chatbot import understand_question, execute_query


st.set_page_config(
    page_title="AI Pricing Copilot",
    page_icon="💰"
)

st.title(" AI Pricing Copilot")

st.write(
    "Ask questions about transactions, expenses, budgets and financial data."
)


if "messages" not in st.session_state:
    st.session_state.messages = []


for message in st.session_state.messages:

    with st.chat_message(message["role"]):
        st.write(message["content"])


question = st.chat_input(
    "Ask a finance question..."
)


if question:

    # Show user question
    st.session_state.messages.append({
        "role": "user",
        "content": question
    })

    with st.chat_message("user"):
        st.write(question)

    try:

        # Step 1: Understand question
        query = understand_question(question)

        # Step 2: Execute against dataset
        result = execute_query(query)

        # Step 3: Display result
        with st.chat_message("assistant"):

            if "message" in result:
                st.write(result["message"])

            if "total" in result:
                st.write(
                    f"Total amount: ₹{result['total']:,.2f}"
                )

            if "average" in result:
                st.write(
                    f"Average amount: ₹{result['average']:,.2f}"
                )

            if "count" in result:
                st.write(
                    f"Number of transactions: {result['count']}"
                )

            if "budget" in result:

                st.write(
                    f"Budget: ₹{result['budget']:,.2f}"
                )

                st.write(
                    f"Actual: ₹{result['actual']:,.2f}"
                )

                st.write(
                    f"Variance: ₹{result['variance']:,.2f}"
                )

            if "data" in result:

                st.dataframe(
                    result["data"],
                    use_container_width=True
                )

    except Exception as e:

        with st.chat_message("assistant"):

            st.write(
                "I could not process that question. "
                "Please try asking about the available financial data."
            )

            st.write(str(e))