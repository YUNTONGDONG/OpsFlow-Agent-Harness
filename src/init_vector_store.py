import os
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# 加载环境变量
load_dotenv()

def init_vector_store():
    # 初始化 Qdrant 客户端
    client = QdrantClient(path=os.getenv("PERSIST_DIR", "./vector_store"))
    collection_name = os.getenv("EMBEDDING_COLLECTION", "langchain_docs")
    
    # 重新创建集合
    try:
        client.recreate_collection(
            collection_name=collection_name,
            vectors_config={
                "size": 1536,  # text-embedding-3-small 的维度
                "distance": "Cosine"
            }
        )
        print(f"Successfully recreated collection: {collection_name}")
    except Exception as e:
        print(f"Error recreating collection: {e}")
    
    # 初始化向量存储
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=OpenAIEmbeddings(
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            api_key=os.getenv("EMBEDDING_API_KEY"),
            base_url=os.getenv("EMBEDDING_API_BASE")
        )
    )
    
    # Langchain 向量数据库的文档
    docs = [
        """
        Langchain 支持多种向量数据库，包括：
        1. Chroma - 一个开源的向量数据库，支持本地存储和持久化
        2. FAISS - Facebook AI 开发的向量搜索库，支持 CPU 和 GPU
        3. Qdrant - 一个高性能的向量搜索引擎，支持过滤和分面搜索
        4. Pinecone - 一个托管的向量数据库服务，提供高可用性和可扩展性
        5. Weaviate - 一个开源的向量搜索引擎，支持多模态数据
        6. Milvus - 一个开源的向量数据库，专注于可扩展性和高性能
        7. Redis - 通过 Redis Stack 支持向量搜索功能
        8. Elasticsearch - 通过向量搜索插件支持向量检索
        """,
        """
        Qdrant 是一个强大的向量搜索引擎，具有以下特点：
        1. 支持多种距离度量方式：Cosine、Euclidean、Dot Product
        2. 支持过滤和分面搜索
        3. 支持实时更新和批量操作
        4. 提供 REST API 和 gRPC 接口
        5. 支持持久化存储
        6. 支持分布式部署
        """,
        """
        Chroma 是一个轻量级的向量数据库，特点包括：
        1. 易于使用和部署
        2. 支持本地存储和持久化
        3. 提供简单的 API
        4. 支持多种嵌入模型
        5. 支持元数据过滤
        6. 支持增量更新
        """,
        """
        FAISS (Facebook AI Similarity Search) 是一个高效的向量搜索库：
        1. 支持 CPU 和 GPU 加速
        2. 提供多种索引类型
        3. 支持批量搜索
        4. 支持量化压缩
        5. 支持多 GPU 并行
        6. 提供 Python 和 C++ 接口
        """
    ]
    
    # 分割文档
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    
    # 添加文档到向量存储
    all_chunks = []
    for doc in docs:
        chunks = text_splitter.split_text(doc)
        all_chunks.extend([Document(page_content=chunk) for chunk in chunks])
    
    print(f"Created {len(all_chunks)} chunks from {len(docs)} documents")
    
    # 批量添加文档
    vector_store.add_documents(all_chunks)
    
    # 验证文档是否添加成功
    test_query = "langchain 的向量数据库"
    results = vector_store.similarity_search(test_query, k=1)
    print(f"Test query results: {len(results)} documents found")
    if results:
        print("Sample content:", results[0].page_content[:200])
    
    print(f"Successfully initialized vector store with {len(docs)} documents")

if __name__ == "__main__":
    init_vector_store() 